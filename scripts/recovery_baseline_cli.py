#!/usr/bin/env python3
# OMNI-PERSISTENT-SCRIPT
# owner: Omnicompany recovery
# purpose: discover verified source baselines and prevent unsafe replay.
"""Resource-bounded baseline discovery and guarded missing-file promotion.

The scanner treats manifests, Git, publish trees, and materialized session
ledgers as evidence.  Historical commands are never executed.  The default
operation is query-only; production writes are only available through a
frozen, hashed plan and only for paths classified ``safe-promote``.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Iterable, Iterator


SCHEMA = "omnicompany.recovery.baseline-plan.v1"
EVIDENCE_CONTRACT = {
    "source_selection": "Only complete post-images (snapshot/Git/materialized full-file artifact) select a baseline by default.",
    "hunks": "A patch/edit is conditionally deterministic only with an exact pre-image hash and an unambiguous ordered chain; it is never promoted from semantics or test results.",
    "terminal_writes": "Historical terminal commands are never re-executed. They require a materialized later post-image before selection.",
    "runnability": "Builds, tests, and browser probes are current-tree observations only; they neither select nor certify the historical latest source.",
}
DEFAULT_SNAPSHOT_ROOTS = (
    "data/domains/game_observatory/explore_dispatch_runs/runtime_snapshots",
    ".omni/snapshots",
    "snapshots",
)
DEFAULT_SESSION_ROOTS = (
    "%USERPROFILE%/.codex",
    "%USERPROFILE%/.claude",
    "%USERPROFILE%/.kimi-code",
    "%APPDATA%/kimi-desktop/daimon-share/daimon/runtime/kimi-code/home",
    "%USERPROFILE%/.local/share/opencode",
    "C:/workspace/omnicompany-recovery-evidence",
)
SOURCE_SUFFIXES = {
    ".bat", ".c", ".cc", ".cfg", ".cjs", ".cmd", ".cpp", ".cs",
    ".css", ".go", ".h", ".hpp", ".html", ".ini", ".java", ".js",
    ".json", ".jsx", ".kt", ".kts", ".lua", ".md", ".mjs", ".ps1",
    ".psd1", ".psm1", ".py", ".pyi", ".rs", ".scss", ".sh", ".sql",
    ".svg", ".toml", ".ts", ".tsx", ".vue", ".xml", ".yaml", ".yml",
}
SOURCE_NAMES = {
    ".env.example", ".gitignore", ".gitattributes", "dockerfile",
    "makefile", "package-lock.json", "package.json", "pyproject.toml",
    "cargo.toml", "cargo.lock",
}
PRUNED_PARTS = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".venv",
    "__pycache__", "coverage", "node_modules", "site-packages", "target",
    "temp", "tmp", "venv",
}
GENERATED_PARTS = {
    "artifacts", "cache", "data", "dist", "exports", "generated", "logs",
    "output", "outputs", "reports", "runtime", "smoke", "snapshots",
}
SESSION_INDEX_NAMES = {
    "history.jsonl", "session_index.jsonl", "transcription-history.jsonl",
    "opencode.db", "workspaces.json",
}
RAW_SESSION_NAME = re.compile(r"(?:rollout-|session-|history).*?(20\d{2}-\d{2}-\d{2})", re.IGNORECASE)
DELETE_FILE = re.compile(r"\*\*\* Delete File:\s*([^\r\n]+)")
ADD_FILE = re.compile(r"\*\*\* Add File:\s*([^\r\n]+)")
EVENT_TIMESTAMP = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')
COMPLETED_STATUS = re.compile(r'"status"\s*:\s*"completed"')
HASHED_RUNTIME_ASSET = re.compile(r".+[-_][A-Za-z0-9_-]{8,}\.(?:css|js|mjs|map)$")
VOLATILE_EVIDENCE_PARTS = {"test-results", "playwright-report", "blob-report"}


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def _utc_iso(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat()


def _now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def _canonical_hash(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "plan_sha256"}
    encoded = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HashCache:
    """Small path/stat keyed hash cache, optionally persisted by explicit opt-in."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path
        self.entries: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        if cache_path and cache_path.is_file():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                self.entries = dict(payload.get("entries") or {})
            except (OSError, json.JSONDecodeError):
                self.entries = {}

    def digest(self, path: Path) -> str:
        stat = path.stat()
        key = str(path.resolve()).casefold()
        cached = self.entries.get(key)
        if cached and cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns:
            self.hits += 1
            return str(cached["sha256"])
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        value = digest.hexdigest()
        self.entries[key] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": value,
        }
        self.misses += 1
        return value

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.cache_path, {"schema": 1, "entries": self.entries})


class TombstoneCache:
    """Path/stat keyed transcript extraction cache, persisted only by opt-in.

    Raw logs can be very large and are append-only in normal operation.  A
    matching size and mtime therefore lets later discovery runs reuse a prior
    completed-patch extraction without re-reading the transcript.  The cache
    stores only deletion evidence, never a replayable shell command.
    """

    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path
        self.entries: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        if cache_path and cache_path.is_file():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                self.entries = dict(payload.get("entries") or {})
            except (OSError, json.JSONDecodeError):
                self.entries = {}

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.resolve()).casefold()

    def lookup(self, path: Path) -> dict[str, Any] | None:
        stat = path.stat()
        cached = self.entries.get(self._key(path))
        if cached and cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns:
            self.hits += 1
            return cached
        self.misses += 1
        return None

    def store(self, path: Path, records: list[dict[str, Any]], skipped_large: bool = False) -> None:
        stat = path.stat()
        self.entries[self._key(path)] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "records": records,
            "skipped_large": skipped_large,
        }

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.cache_path, {"schema": 1, "entries": self.entries})


class LedgerCache(TombstoneCache):
    """Path/stat cache for materialized complete-file ledger records."""

    def lookup_for_workspace(self, path: Path, workspace: Path) -> dict[str, Any] | None:
        stat = path.stat()
        cached = self.entries.get(self._key(path))
        if cached and cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns:
            if cached.get("workspace_root") == str(workspace.resolve()):
                self.hits += 1
                return cached
        self.misses += 1
        return None

    def store_for_workspace(self, path: Path, workspace: Path, records: list[dict[str, Any]]) -> None:
        self.store(path, records)
        self.entries[self._key(path)]["workspace_root"] = str(workspace.resolve())


def _lower_process_priority() -> str:
    if os.name != "nt":
        try:
            os.nice(10)
            return "nice+10"
        except OSError:
            return "unchanged"
    try:
        below_normal_priority_class = 0x00004000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        if kernel32.SetPriorityClass(handle, below_normal_priority_class):
            return "below-normal"
    except (AttributeError, OSError):
        pass
    return "unchanged"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_relative(raw: str, workspace: Path) -> str | None:
    normalized = raw.replace("\\", "/").strip().lstrip("./")
    if not normalized:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            normalized = candidate.resolve().relative_to(workspace).as_posix()
        except ValueError:
            return None
    if normalized.startswith("../") or "/../" in normalized:
        return None
    workspace_prefix = workspace.name.casefold() + "/"
    if normalized.casefold().startswith(workspace_prefix):
        normalized = normalized[len(workspace.name) + 1:]
    return normalized


def _path_prefixes(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        candidate = str(value).replace("\\", "/").strip("/")
        if candidate and not candidate.startswith("../") and candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _matches_path_prefix(relative: str, prefixes: tuple[str, ...]) -> bool:
    return not prefixes or any(relative == prefix or relative.startswith(prefix + "/") for prefix in prefixes)


def _is_ephemeral_recovery_path(relative: str) -> bool:
    """Keep one-off probes in evidence without treating them as product loss."""
    normalized = relative.replace("\\", "/").casefold()
    name = Path(normalized).name
    if not normalized.startswith("src/omnicompany/dashboard/"):
        return False
    markers = ("audit", "shoot", "verify", "probe", "debug", "demo", "tmp", "smoke")
    if name.startswith(".") and any(marker in name for marker in markers):
        return True
    if name.startswith("_") and any(marker in name for marker in markers):
        return True
    frontend_root_probe = "/dashboard/frontend/" in normalized and "/" not in normalized.rsplit("/dashboard/frontend/", 1)[1]
    if frontend_root_probe and name.startswith((".", "_")) and Path(name).suffix in {".mjs", ".py"}:
        return True
    return "/tests/e2e/visual/" in normalized and (name.startswith("_") or name.startswith("v2-wave"))


def _is_source_path(relative: str, *, allow_generated_root: bool = False) -> bool:
    path = Path(relative.replace("\\", "/"))
    parts = {part.casefold() for part in path.parts[:-1]}
    if parts & PRUNED_PARTS:
        return False
    if not allow_generated_root and parts & GENERATED_PARTS:
        return False
    # E2E reports are evidence, not product source.  Vite-style content-hashed
    # static bundles are likewise derivable from the frontend source and must
    # never consume a promotion slot merely because an older deployment kept
    # a complete copy.
    if parts & VOLATILE_EVIDENCE_PARTS:
        return False
    if "static" in parts and "assets" in parts and HASHED_RUNTIME_ASSET.fullmatch(path.name):
        return False
    name = path.name.casefold()
    if name.endswith((".pyc", ".pyo", ".log", ".tmp", ".bak")):
        return False
    return name in SOURCE_NAMES or path.suffix.casefold() in SOURCE_SUFFIXES


def _walk_source_files(root: Path, *, allow_generated_root: bool = False) -> Iterator[tuple[str, Path]]:
    if not root.is_dir():
        return
    for current, directories, files in os.walk(root):
        directories[:] = [
            name for name in directories
            if name.casefold() not in PRUNED_PARTS | GENERATED_PARTS
        ]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if _is_source_path(relative, allow_generated_root=allow_generated_root):
                yield relative, path


def _config(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.config:
        try:
            payload = json.loads(args.config.resolve().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid baseline discovery config: {exc}") from exc
    snapshot_roots = list(payload.get("snapshot_roots") or [])
    snapshot_roots.extend(str(path) for path in args.snapshot_root)
    if not snapshot_roots:
        snapshot_roots = [str(workspace / relative) for relative in DEFAULT_SNAPSHOT_ROOTS]
    session_roots = list(payload.get("session_roots") or [])
    session_roots.extend(str(path) for path in args.session_root)
    if not session_roots:
        session_roots = list(DEFAULT_SESSION_ROOTS)
    candidate_roots = list(payload.get("candidate_roots") or [])
    candidate_roots.extend(
        {"path": str(path), "source": "candidate-tree"} for path in args.candidate_root
    )
    candidate_roots.extend(_auto_candidate_roots(workspace, deep=args.discover_candidate_roots))
    return {
        "snapshot_roots": [str(_expand(str(item))) for item in snapshot_roots],
        "session_roots": [str(_expand(str(item))) for item in session_roots],
        "candidate_roots": _dedupe_candidate_roots(candidate_roots),
    }


def _dedupe_candidate_roots(items: Iterable[object]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            record = {"path": str(_expand(item)), "source": "candidate-tree"}
        elif isinstance(item, dict) and item.get("path"):
            record = {
                "path": str(_expand(str(item["path"]))),
                "source": str(item.get("source") or "candidate-tree"),
            }
        else:
            continue
        identity = record["path"].casefold()
        if identity not in seen:
            seen.add(identity)
            result.append(record)
    return result


def _auto_candidate_roots(workspace: Path, *, deep: bool = False) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for name, source in (
        ("publish", "publish-tree"), ("published", "publish-tree"),
        ("dist", "build-tree"), ("build", "build-tree"),
    ):
        path = workspace / name
        if path.is_dir():
            candidates.append({"path": str(path), "source": source})
    public_parent = workspace.parent / "发布"
    if public_parent.is_dir():
        for child in public_parent.iterdir():
            if child.is_dir() and ("omni" in child.name.casefold() or "public" in child.name.casefold()):
                candidates.append({"path": str(child), "source": "publish-tree"})
    if not deep:
        return candidates
    for current, directories, _files in os.walk(workspace):
        relative_depth = len(Path(current).relative_to(workspace).parts)
        if relative_depth > 4:
            directories[:] = []
            continue
        for name in list(directories):
            folded = name.casefold()
            if folded in {"dist", "build"}:
                candidates.append({"path": str(Path(current) / name), "source": "build-tree"})
        directories[:] = [
            name for name in directories
            if name.casefold() not in PRUNED_PARTS | GENERATED_PARTS
        ]
    return candidates


def _discover_manifests(roots: Iterable[str], prefix: str | None, *, deep: bool = False) -> list[Path]:
    manifests: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw)
        if root.is_file() and root.name == "snapshot-manifest.json":
            found = [root]
        elif root.is_dir() and prefix and not deep:
            # A prefix identifies a recovery cohort.  Recursing through every
            # runtime snapshot defeats the resource bound of the default scan.
            found = [root / "snapshot-manifest.json"]
            try:
                found.extend(
                    child / "snapshot-manifest.json" for child in root.iterdir()
                    if child.is_dir() and child.name.casefold().startswith(prefix.casefold())
                )
            except OSError:
                pass
        elif root.is_dir():
            found = list(root.rglob("snapshot-manifest.json"))
        else:
            found = []
        for manifest in found:
            if prefix and not (
                manifest.parent.name.casefold().startswith(prefix.casefold())
                or prefix.casefold() in str(manifest).casefold()
            ):
                continue
            identity = str(manifest.resolve()).casefold()
            if identity not in seen:
                seen.add(identity)
                manifests.append(manifest.resolve())
    return sorted(manifests, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def _manifest_records(
    manifest: Path,
    workspace: Path,
    path_prefixes: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], {"path": str(manifest), "valid": False, "error": str(exc)}
    raw_files = payload.get("files") or []
    if isinstance(raw_files, dict):
        raw_files = [dict(value, path=key) if isinstance(value, dict) else {"path": key, "sha256": value}
                     for key, value in raw_files.items()]
    timestamp = _utc_iso(manifest.stat().st_mtime)
    records: list[dict[str, Any]] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            continue
        relative = _safe_relative(str(raw.get("path") or raw.get("relative_path") or ""), workspace)
        # Apply an explicitly bounded batch before probing the snapshot
        # artifact.  A manifest can describe tens of thousands of files; doing
        # ``is_file``/``stat`` first defeats --path-prefix's resource bound.
        if (
            not relative
            or not _matches_path_prefix(relative, path_prefixes)
            or not _is_source_path(relative)
        ):
            continue
        artifact = manifest.parent / relative
        expected = str(raw.get("sha256") or raw.get("hash") or "")
        size = raw.get("size_bytes", raw.get("size"))
        accessible = artifact.is_file()
        size_match = accessible and (size is None or artifact.stat().st_size == int(size))
        records.append({
            "path": relative,
            "timestamp": timestamp,
            "source": "snapshot-manifest",
            "source_id": str(payload.get("source_manifest_sha256") or manifest.parent.name),
            "hash": expected or None,
            "hash_algorithm": "sha256",
            "size_bytes": size,
            "artifact": str(artifact.resolve()),
            "accessible": accessible,
            "complete": bool(expected and size_match),
            "confidence": 1.0 if expected and size_match else (0.45 if accessible else 0.0),
        })
    return records, {
        "path": str(manifest),
        "valid": True,
        "source_id": str(payload.get("source_manifest_sha256") or manifest.parent.name),
        "declared_files": len(raw_files),
        "source_candidates": len(records),
        "complete_source_candidates": sum(bool(record["complete"]) for record in records),
        "timestamp": timestamp,
    }


def _discover_session_inventory(roots: Iterable[str]) -> tuple[list[dict[str, Any]], list[Path]]:
    inventory: list[dict[str, Any]] = []
    ledgers: list[Path] = []
    for raw in roots:
        root = Path(raw)
        record = {"path": str(root), "exists": root.exists(), "indexes": 0, "ledgers": 0}
        if root.is_dir():
            # Provider indexes live at predictable shallow locations.  Do not
            # recursively read raw session payloads merely to discover them.
            index_candidates = [root / name for name in SESSION_INDEX_NAMES]
            index_candidates.extend(root / "sessions" / name for name in SESSION_INDEX_NAMES)
            record["indexes"] = sum(path.is_file() for path in index_candidates)
            # Recovery evidence is already materialized and safe to enumerate;
            # provider homes are deliberately not deep-walked.  Even evidence
            # roots may contain multi-gigabyte object stores, so ledger
            # discovery is bounded and prunes payload-bearing directories.
            if "recovery-evidence" in root.name.casefold() or root.name.casefold() in {"evidence", "ledgers"}:
                for current, directories, files in os.walk(root):
                    depth = len(Path(current).relative_to(root).parts)
                    if depth >= 3:
                        directories[:] = []
                    else:
                        directories[:] = [
                            name for name in directories
                            if name.casefold() not in (
                                PRUNED_PARTS
                                | GENERATED_PARTS
                                | {
                                    "archive", "archives", "candidate-files", "candidates",
                                    "objects", "payloads", "raw", "session-archive",
                                }
                            )
                        ]
                    if "LATEST-COMPLETE-FILES.json" in files:
                        ledgers.append((Path(current) / "LATEST-COMPLETE-FILES.json").resolve())
                        record["ledgers"] += 1
        inventory.append(record)
    return inventory, sorted(set(ledgers), key=lambda path: str(path).casefold())


def _ledger_records(ledger: Path, workspace: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    result: list[dict[str, Any]] = []
    for raw in payload.get("files") or []:
        if not isinstance(raw, dict):
            continue
        relative = _safe_relative(str(raw.get("path") or raw.get("normalized_path") or ""), workspace)
        if not relative or not _is_source_path(relative):
            continue
        artifact_value = raw.get("candidate_artifact")
        artifact = Path(str(artifact_value)).resolve() if artifact_value else None
        expected = str(raw.get("candidate_sha256") or raw.get("sha256") or "")
        accessible = bool(artifact and artifact.is_file())
        result.append({
            "path": relative,
            "timestamp": str(raw.get("timestamp") or _utc_iso(ledger.stat().st_mtime)),
            "source": "session-ledger",
            "source_id": str(raw.get("session_id") or ledger.parent.name),
            "hash": expected or None,
            "hash_algorithm": "sha256",
            "size_bytes": artifact.stat().st_size if accessible and artifact else None,
            "artifact": str(artifact) if artifact else None,
            "accessible": accessible,
            "complete": bool(expected and accessible),
            "confidence": 0.97 if expected and accessible else 0.1,
            "ledger": str(ledger),
        })
    return result


def _cached_ledger_records(ledger: Path, workspace: Path, cache: LedgerCache | None) -> list[dict[str, Any]]:
    try:
        cached = cache.lookup_for_workspace(ledger, workspace) if cache else None
        if cached is not None:
            return list(cached.get("records") or [])
        records = _ledger_records(ledger, workspace)
        if cache:
            cache.store_for_workspace(ledger, workspace, records)
        return records
    except OSError:
        return []


def _git_head_records(
    workspace: Path,
    relative_filter: set[str] | None = None,
    path_prefixes: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=workspace,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if probe.returncode != 0:
        return [], {"available": False, "error": probe.stderr.strip()}
    git_root = Path(probe.stdout.strip()).resolve()
    if git_root != workspace:
        return [], {"available": False, "error": f"workspace is nested under Git root {git_root}"}
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if head.returncode != 0:
        return [], {"available": False, "error": head.stderr.strip()}
    commit = head.stdout.strip()
    stamp_proc = subprocess.run(
        ["git", "show", "-s", "--format=%cI", commit], cwd=workspace,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    timestamp = stamp_proc.stdout.strip() or _utc_iso(0)
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", commit], cwd=workspace,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if tree.returncode != 0:
        return [], {"available": False, "error": tree.stderr.decode(errors="replace")}
    entries: list[tuple[str, str]] = []
    for item in tree.stdout.split(b"\0"):
        if not item or b"\t" not in item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        fields = metadata.decode("ascii", errors="replace").split()
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        if (
            len(fields) >= 3
            and fields[1] == "blob"
            and _is_source_path(relative)
            and (relative_filter is None or relative in relative_filter)
            and _matches_path_prefix(relative, path_prefixes)
        ):
            entries.append((relative, fields[2]))
    sha256_by_blob: dict[str, tuple[str, int]] = {}
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=workspace,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        for blob in dict.fromkeys(blob for _relative, blob in entries):
            process.stdin.write((blob + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", errors="replace").strip().split()
            if len(header) < 3 or header[1] != "blob":
                continue
            size = int(header[2])
            value = process.stdout.read(size)
            process.stdout.read(1)
            sha256_by_blob[blob] = (_sha256_bytes(value), size)
    finally:
        process.stdin.close()
        process.wait(timeout=30)
    records = []
    for relative, blob in entries:
        digest_size = sha256_by_blob.get(blob)
        if not digest_size:
            continue
        digest, size = digest_size
        records.append({
            "path": relative,
            "timestamp": timestamp,
            "source": "git-head",
            "source_id": commit,
            "hash": digest,
            "hash_algorithm": "sha256",
            "git_blob": blob,
            "size_bytes": size,
            "artifact": None,
            "accessible": True,
            "complete": True,
            "confidence": 0.99,
        })
    return records, {
        "available": True,
        "root": str(git_root),
        "head": commit,
        "candidates": len(records),
        "path_filter_applied": relative_filter is not None,
        "path_prefixes": list(path_prefixes),
    }


def _git_blob_bytes(workspace: Path, source_id: str, relative: str) -> bytes:
    """Read a frozen Git post-image without checking it out or executing history.

    A Git tree is a complete file artifact even when it has no separately
    materialized candidate path.  The caller verifies the resulting SHA-256
    against the frozen plan before it is staged.  ``relative`` has already
    passed workspace containment checks during plan construction.
    """

    if not source_id or not relative:
        raise SystemExit("apply refused: incomplete Git post-image reference")
    result = subprocess.run(
        ["git", "show", f"{source_id}:{relative}"], cwd=workspace,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(f"apply refused: Git post-image is unavailable for {relative}: {detail}")
    return result.stdout


def _git_changed_paths(workspace: Path) -> set[str] | None:
    """Return tracked paths with logical Git changes, respecting clean filters.

    Raw SHA-256 values are intentionally byte-exact for recovery artifacts, but
    they cannot equate a CRLF worktree checkout with its LF Git blob.  Git's
    own comparison applies the repository's clean filters, which is the
    authoritative way to recognize an otherwise clean Windows checkout.
    """

    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD", "--"], cwd=workspace,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        return None
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }


def _tree_records(
    root: Path,
    source: str,
    cache: HashCache,
    workers: int,
    workspace: Path,
    relative_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    if relative_filter is None:
        files = list(_walk_source_files(root, allow_generated_root=source == "publish-tree"))
    else:
        files = []
        for relative in sorted(relative_filter, key=str.casefold):
            if not _is_source_path(relative, allow_generated_root=source == "publish-tree"):
                continue
            path = (root / relative).resolve()
            if _is_relative_to(path, root.resolve()) and path.is_file():
                files.append((relative, path))

    def build(row: tuple[str, Path]) -> dict[str, Any]:
        relative, path = row
        stat = path.stat()
        return {
            "path": relative,
            "timestamp": _utc_iso(stat.st_mtime),
            "source": source,
            "source_id": str(root),
            "hash": cache.digest(path),
            "hash_algorithm": "sha256",
            "size_bytes": stat.st_size,
            "artifact": str(path.resolve()),
            "accessible": True,
            "complete": True,
            "confidence": 0.97 if root == workspace else (0.86 if source == "publish-tree" else 0.65),
        }

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="recovery-hash") as pool:
        return list(pool.map(build, files))


def _raw_session_sort_key(path: Path) -> tuple[str, int, str]:
    match = RAW_SESSION_NAME.search(path.name)
    return (match.group(1) if match else "", path.stat().st_mtime_ns, str(path).casefold())


def _discover_raw_sessions(roots: Iterable[str], max_files: int, priority_ids: set[str]) -> tuple[list[Path], set[str]]:
    """Find only recent provider transcripts; never descend into arbitrary payload trees."""
    found: dict[str, Path] = {}
    allowed_names = {"sessions", "projects", "history", "transcripts", "codex", "claude", "kimi", "opencode"}
    skipped = PRUNED_PARTS | GENERATED_PARTS | {"archive", "archives", "objects", "payloads", "raw", "candidate-files", "candidates"}
    for raw in roots:
        root = Path(raw)
        if root.is_file() and root.suffix.casefold() == ".jsonl":
            found[str(root.resolve()).casefold()] = root.resolve()
            continue
        if not root.is_dir():
            continue
        for current, directories, files in os.walk(root):
            relative_parts = Path(current).relative_to(root).parts
            depth = len(relative_parts)
            if depth > 6:
                directories[:] = []
                continue
            directories[:] = [
                name for name in directories
                if name.casefold() not in skipped
                and (depth < 2 or name.casefold() in allowed_names or depth < 5)
            ]
            for name in files:
                lower = name.casefold()
                if not lower.endswith(".jsonl"):
                    continue
                if not (lower.startswith("rollout-") or "session" in lower or "history" in lower or lower == "wire.jsonl"):
                    continue
                path = (Path(current) / name).resolve()
                found[str(path).casefold()] = path
    def rank(path: Path) -> tuple[bool, str, int, str]:
        folded = str(path).casefold()
        return (any(identity.casefold() in folded for identity in priority_ids if len(identity) >= 8), *_raw_session_sort_key(path))
    newest = sorted(found.values(), key=rank, reverse=True)
    available_ids = {
        identity for identity in priority_ids
        if len(identity) >= 8 and any(identity.casefold() in str(path).casefold() for path in newest)
    }
    return newest[:max(0, max_files)], available_ids


def _session_delete_records(
    roots: Iterable[str], workspace: Path, max_files: int, max_bytes: int, priority_ids: set[str],
    cache: TombstoneCache | None = None, explicit_includes: Iterable[Path] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract completed apply_patch deletions as tombstones without replaying commands.

    An Add File in the same patch makes the operation a replacement, not a
    deletion.  Shell remove commands are intentionally not elevated to
    tombstones: their execution outcome and path expansion are not reliable
    enough for automated destructive decisions.
    """
    paths, available_ids = _discover_raw_sessions(roots, max_files, priority_ids)
    explicit_paths: list[Path] = []
    seen = {str(path.resolve()).casefold() for path in paths}
    for raw in explicit_includes:
        path = raw.resolve()
        if not path.is_file() or path.suffix.casefold() != ".jsonl":
            raise SystemExit(f"--raw-tombstone-include must name an existing .jsonl transcript: {path}")
        identity = str(path).casefold()
        if identity not in seen:
            seen.add(identity)
            paths.append(path)
            explicit_paths.append(path)
    scanned_ids = {
        identity for identity in priority_ids
        if len(identity) >= 8 and any(identity.casefold() in str(path).casefold() for path in paths)
    }
    records: list[dict[str, Any]] = []
    scanned = 0
    skipped_large = 0
    for path in paths:
        try:
            cached = cache.lookup(path) if cache else None
            if cached is not None:
                records.extend(list(cached.get("records") or []))
                scanned += 1
                if cached.get("skipped_large"):
                    skipped_large += 1
                continue
            if path.stat().st_size > max_bytes:
                skipped_large += 1
                if cache:
                    cache.store(path, [], skipped_large=True)
                continue
            session_records: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if "*** Delete File:" not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload") if isinstance(event, dict) else None
                    if not isinstance(payload, dict) or payload.get("status") != "completed":
                        continue
                    patch_text = str(payload.get("input") or "")
                    if "*** Delete File:" not in patch_text:
                        continue
                    deleted = {match.strip() for match in DELETE_FILE.findall(patch_text)}
                    added = {match.strip() for match in ADD_FILE.findall(patch_text)}
                    timestamp = str(event.get("timestamp") or _utc_iso(path.stat().st_mtime))
                    for raw_path in sorted(deleted - added):
                        relative = _safe_relative(raw_path, workspace)
                        if not relative or not _is_source_path(relative):
                            continue
                        session_records.append({
                            "path": relative,
                            "timestamp": timestamp,
                            "source": "session-delete",
                            "source_id": str(path),
                            "hash": None,
                            "hash_algorithm": None,
                            "size_bytes": None,
                            "artifact": None,
                            "accessible": True,
                            "complete": False,
                            "tombstone": True,
                            "confidence": 0.99,
                        })
            records.extend(session_records)
            if cache:
                cache.store(path, session_records)
            scanned += 1
        except OSError:
            continue
    return records, {
        "enabled": True,
        "candidate_sessions": len(paths),
        "explicit_includes": [str(path) for path in explicit_paths],
        "priority_session_ids": len(priority_ids),
        "available_priority_session_ids": sorted(available_ids),
        "scanned_priority_session_ids": sorted(scanned_ids),
        "scanned_sessions": scanned,
        "skipped_large_sessions": skipped_large,
        "max_bytes_per_session": max_bytes,
        "tombstones": len(records),
        "cache_hits": cache.hits if cache else 0,
        "cache_misses": cache.misses if cache else 0,
        "cache_configured": bool(cache and cache.cache_path),
    }


def _insert_snapshot_chains(
    chains: dict[str, list[dict[str, Any]]],
    records: Iterable[dict[str, Any]],
    snapshot_state: dict[str, dict[str, Any]],
    max_chain: int,
) -> None:
    for record in records:
        path = record["path"]
        state = snapshot_state.setdefault(path, {"converged": False, "dropped": 0})
        if state["converged"] or len([row for row in chains.get(path, []) if row["source"] == "snapshot-manifest"]) >= max_chain:
            state["dropped"] += 1
            continue
        chain = chains.setdefault(path, [])
        previous = next((row for row in reversed(chain) if row["source"] == "snapshot-manifest"), None)
        chain.append(record)
        if previous and previous.get("hash") and previous.get("hash") == record.get("hash"):
            state["converged"] = True


def _snapshot_current_convergence(
    chains: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return a narrow snapshot/worktree/Git observation for selected paths.

    This is intentionally *not* a recovery-completeness decision.  It answers
    only whether each source path materialized by the selected snapshot has an
    extant worktree file with the same declared SHA-256.  A later session may
    still contain a session-only path, a tombstone, or an unmaterialized
    post-image, so callers must report those sources as deferred rather than
    calling the repository or the snapshot "complete".
    """
    snapshot_paths: list[str] = []
    matching: list[str] = []
    unresolved: list[str] = []
    git_matching = 0
    git_different_or_missing = 0
    for relative in sorted(chains, key=str.casefold):
        snapshot = next((row for row in chains[relative] if row.get("source") == "snapshot-manifest"), None)
        if not snapshot:
            continue
        snapshot_paths.append(relative)
        worktree = next((row for row in chains[relative] if row.get("source") == "worktree"), None)
        if (
            snapshot.get("complete")
            and snapshot.get("accessible")
            and worktree
            and worktree.get("hash") == snapshot.get("hash")
        ):
            matching.append(relative)
        else:
            unresolved.append(relative)
        git = next((row for row in chains[relative] if row.get("source") == "git-head"), None)
        if git and git.get("hash") == snapshot.get("hash"):
            git_matching += 1
        else:
            git_different_or_missing += 1
    return {
        "snapshot_paths_checked": len(snapshot_paths),
        "snapshot_paths_matching_worktree": len(matching),
        "snapshot_paths_matching_git_head": git_matching,
        "snapshot_paths_git_head_different_or_missing": git_different_or_missing,
        "unresolved_snapshot_paths": unresolved,
        "all_selected_snapshot_paths_match_worktree": bool(snapshot_paths) and not unresolved,
    }


def _expanded_worktree_paths(paths: Iterable[str]) -> set[str]:
    """Include same-stem module alternatives for shadowing classification."""
    expanded = set(paths)
    for relative in tuple(expanded):
        suffix = Path(relative).suffix.casefold()
        stem = relative[: -len(suffix)] if suffix else relative
        for alternative in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts"}:
            expanded.add(stem + alternative)
    return expanded


def _merge_case_equivalent_chains(
    chains: dict[str, list[dict[str, Any]]], preferred_paths: Iterable[str],
) -> int:
    """Collapse Windows session path aliases onto Git/worktree spelling."""

    if os.name != "nt":
        return 0
    canonical: dict[str, str] = {}
    for relative in preferred_paths:
        canonical.setdefault(relative.casefold(), relative)
    merged = 0
    for relative in list(chains):
        target = canonical.get(relative.casefold())
        if not target or target == relative:
            continue
        records = chains.pop(relative)
        for record in records:
            record["path"] = target
        chains.setdefault(target, []).extend(records)
        merged += 1
    return merged


def _classify(
    relative: str,
    chain: list[dict[str, Any]],
    snapshot_state: dict[str, dict[str, Any]],
    tombstone_coverage: dict[str, set[str]] | None = None,
    existing_paths: set[str] | None = None,
    git_changed_paths: set[str] | None = None,
    include_chain: bool = False,
) -> tuple[str, dict[str, Any]]:
    ordered = sorted(
        chain,
        key=lambda row: (str(row.get("timestamp") or ""), float(row.get("confidence") or 0), str(row.get("source"))),
        reverse=True,
    )
    worktree = next((row for row in ordered if row["source"] == "worktree"), None)
    tombstones = [row for row in ordered if row.get("tombstone")]
    evidence = [
        row for row in ordered
        if row["source"] != "worktree" and row.get("complete") and row.get("accessible") and row.get("hash")
    ]
    baseline = evidence[0] if evidence else None
    latest_tombstone = tombstones[0] if tombstones else None
    snapshots = [row for row in evidence if row["source"] == "snapshot-manifest"]
    snapshot_baseline = snapshots[0] if snapshots else None
    # A filesystem mtime is not provenance: checkout, restore, copy, and
    # extraction all refresh it.  Only evidence with its own causal/source
    # timestamp may prove a post-snapshot overlay.
    proven_overlay = None
    if worktree and snapshot_baseline:
        proven_overlay = next(
            (
                row for row in evidence
                if row["source"] in {"session-ledger", "git-head"}
                and str(row.get("timestamp")) > str(snapshot_baseline.get("timestamp"))
                and row.get("hash") == worktree.get("hash")
                and row.get("hash") != snapshot_baseline.get("hash")
            ),
            None,
        )
    inaccessible = [row for row in ordered if row["source"] != "worktree" and not row.get("accessible")]
    hashes: dict[str, set[str]] = {}
    for row in evidence:
        hashes.setdefault(str(row["hash"]), set()).add(str(row["source"]))
    converged = bool(snapshot_state.get(relative, {}).get("converged")) or any(len(sources) >= 2 for sources in hashes.values())
    selected_session_id = str(baseline.get("source_id") or "") if baseline and baseline.get("source") == "session-ledger" else ""
    awaiting_tombstone = bool(
        not worktree and baseline and tombstone_coverage
        and bool(tombstone_coverage.get("enabled"))
        and selected_session_id
        and selected_session_id not in tombstone_coverage.get("scanned", set())
    )
    shadowed_by = _module_sibling(relative, existing_paths or set())
    git_clean_equivalent = bool(
        worktree
        and git_changed_paths is not None
        and relative not in git_changed_paths
        and any(row.get("source") == "git-head" for row in evidence)
    )
    if latest_tombstone and (not baseline or str(latest_tombstone.get("timestamp")) >= str(baseline.get("timestamp"))):
        if worktree:
            queue = "tombstone_conflict_manual"
            reason = "a later completed session deletion conflicts with an extant worktree file"
        else:
            queue = "intentionally_removed"
            reason = "a later completed session deletion supersedes older complete file evidence"
    elif _is_ephemeral_recovery_path(relative):
        queue = "ephemeral_evidence"
        reason = "one-off dashboard probe/smoke candidate is retained as evidence, not promoted as product source"
    elif awaiting_tombstone:
        queue = "awaiting_tombstone_scan"
        reason = "candidate session is locally available but not yet covered by the bounded deletion scan"
    elif shadowed_by:
        queue = "module_shadowed_manual"
        reason = f"missing module path is shadowed by existing sibling module: {shadowed_by}"
    elif git_clean_equivalent:
        queue = "identical_converged"
        reason = "Git clean-filter confirms the worktree matches its tracked post-image despite raw-byte conversion"
    elif worktree and snapshot_baseline and worktree.get("hash") == snapshot_baseline.get("hash"):
        queue = "identical_converged"
        reason = "worktree hash equals newest complete snapshot baseline"
    elif worktree and proven_overlay:
        queue = "post_baseline_overlay"
        reason = "post-snapshot Git/session evidence proves the current differing hash"
    elif worktree and not snapshot_baseline and baseline and worktree.get("hash") == baseline.get("hash"):
        queue = "identical_converged"
        reason = "worktree hash equals newest complete evidence"
    elif not worktree and baseline:
        top_timestamp = str(baseline.get("timestamp"))
        top_hashes = {str(row["hash"]) for row in evidence if str(row.get("timestamp")) == top_timestamp}
        if len(top_hashes) == 1 and float(baseline.get("confidence") or 0) >= 0.85:
            queue = "safe_promote"
            reason = "path is missing and newest complete evidence has one unambiguous hash"
        else:
            queue = "conflict_manual"
            reason = "newest complete evidence is ambiguous"
    elif not baseline and inaccessible:
        queue = "missing_source"
        reason = "evidence refers to content whose post-image is unavailable"
    elif not baseline:
        queue = "missing_source" if not worktree else "conflict_manual"
        reason = "no complete non-worktree baseline is available"
    else:
        queue = "conflict_manual"
        reason = "worktree differs from baseline without a safe temporal ordering"
    result = {
        "path": relative,
        "reason": reason,
        "converged": converged,
        "selected": proven_overlay or baseline,
        "tombstone": latest_tombstone,
        "snapshot_baseline": snapshot_baseline,
        "candidate_count": len(ordered),
        "older_snapshot_candidates_skipped": int(snapshot_state.get(relative, {}).get("dropped", 0)),
        "recovery_status": {
            "source_recovery": (
                "exact_source_selected" if baseline and baseline.get("complete") else "no_exact_source_selected"
            ),
            "current_tree_runnability": "not_evaluated",
            "historical_source_correctness": "not_inferable_from_scan",
            "selection_rule": "complete post-image and timestamp/hash chain only",
        },
    }
    if include_chain:
        result["chain"] = ordered
    return queue, result


def _recovery_efficiency(
    manifests: list[dict[str, Any]],
    queues: dict[str, list[dict[str, Any]]],
    snapshot_state: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Summarize automatic convergence and select the next bounded batches.

    This is deliberately computed from already indexed evidence.  It does not
    start another filesystem/session traversal merely to make a recommendation,
    so it is safe to run after every small recovery cohort.
    """
    queue_counts = {name: len(rows) for name, rows in queues.items()}
    paths_considered = sum(queue_counts.values())
    closed_names = (
        "identical_converged", "post_baseline_overlay", "intentionally_removed",
        "module_shadowed_manual", "ephemeral_evidence",
    )
    automatically_closed = sum(queue_counts.get(name, 0) for name in closed_names)
    needs_decision = paths_considered - automatically_closed

    cohorts = []
    for row in manifests:
        declared = int(row.get("declared_files") or 0)
        candidates = int(row.get("source_candidates") or 0)
        complete = int(row.get("complete_source_candidates") or 0)
        cohorts.append({
            "source_id": row.get("source_id"),
            "timestamp": row.get("timestamp"),
            "manifest": row.get("path"),
            "declared_files": declared,
            "source_candidates": candidates,
            "complete_source_candidates": complete,
            "source_coverage_ratio": round(complete / candidates, 4) if candidates else 0.0,
            "complete_baseline": bool(row.get("valid") and candidates and complete == candidates),
        })

    def path_bucket(path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if not parts:
            return "."
        # Keep product areas together while not producing one recommendation
        # per leaf directory.  This makes dense recent batches visible.
        return "/".join(parts[: min(3, len(parts))])

    recommendations: list[dict[str, Any]] = []
    priorities = {
        "safe_promote": 100,
        "awaiting_tombstone_scan": 80,
        "conflict_manual": 55,
        "missing_source": 35,
        "tombstone_conflict_manual": 25,
    }
    for queue_name, priority in priorities.items():
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in queues.get(queue_name, []):
            buckets.setdefault(path_bucket(str(row.get("path") or "")), []).append(row)
        for bucket, rows in buckets.items():
            selected_source_ids = sorted({
                str((row.get("selected") or {}).get("source_id") or "")
                for row in rows
                if (row.get("selected") or {}).get("source_id")
            })
            recommendations.append({
                "queue": queue_name,
                "path_prefix": bucket,
                "paths": len(rows),
            # A safe missing post-image is more valuable than a large
            # undifferentiated conflict bucket: it can retire old history
            # immediately.  Density breaks ties only within the same action.
            "priority_score": priority * 100 + min(len(rows), 49),
                "source_ids": selected_source_ids[:8],
                "action": (
                    "review frozen apply plan" if queue_name == "safe_promote"
                    else "scan selected-session tombstones only" if queue_name == "awaiting_tombstone_scan"
                    else "run focused functional probe before content review" if queue_name == "conflict_manual"
                    else "locate exact post-image from the listed provider/session roots"
                ),
            })
    recommendations.sort(
        key=lambda row: (-int(row["priority_score"]), -int(row["paths"]), str(row["path_prefix"]).casefold())
    )

    converged_paths = sum(bool(state.get("converged")) for state in snapshot_state.values())
    skipped = sum(int(state.get("dropped", 0)) for state in snapshot_state.values())
    return {
        "paths_considered": paths_considered,
        "automatically_closed_paths": automatically_closed,
        "requires_new_content_decision": needs_decision,
        "automatic_closure_ratio": round(automatically_closed / paths_considered, 4) if paths_considered else 1.0,
        "complete_baseline_cohorts": cohorts,
        # `latest_fullest_baseline` is always present when a manifest was
        # found.  `latest_complete_baseline` remains strict; callers cannot
        # mistake a 99%-available snapshot for a byte-complete baseline.
        "latest_fullest_baseline": next(iter(cohorts), None),
        "latest_complete_baseline": next((row for row in cohorts if row["complete_baseline"]), None),
        "snapshot_convergence": {
            "paths_converged": converged_paths,
            "older_candidates_skipped": skipped,
            "older_window_policy": (
                "stay in newest cohort; expand only for failed probes or exact-post-image gaps"
                if converged_paths or skipped else "newer complete cohort has not converged yet"
            ),
        },
        "recommended_next_batches": recommendations[:12],
    }


def discover_baselines(args: argparse.Namespace) -> int:
    started = dt.datetime.now(tz=dt.timezone.utc)
    phase_started = time.perf_counter()
    phase_seconds: dict[str, float] = {}

    def mark_phase(name: str) -> None:
        nonlocal phase_started
        now = time.perf_counter()
        phase_seconds[name] = round(now - phase_started, 3)
        phase_started = now
        if args.progress_file:
            _atomic_json(args.progress_file.resolve(), {
                "schema": "omnicompany.recovery.baseline-progress.v1",
                "mode": "query-only",
                "production_files_written": False,
                "started_at": started.isoformat(),
                "last_completed_phase": name,
                "phase_seconds": phase_seconds,
            })

    workspace = args.workspace_root.resolve()
    if not workspace.is_dir():
        raise SystemExit(f"workspace root not found: {workspace}")
    priority = "normal" if args.normal_priority else _lower_process_priority()
    workers = max(1, min(int(args.workers), 2))
    config = _config(args, workspace)
    path_prefixes = _path_prefixes(args.path_prefix)
    cache = HashCache(args.hash_cache.resolve() if args.hash_cache else None)
    tombstone_cache = TombstoneCache(args.tombstone_cache.resolve() if args.tombstone_cache else None)
    ledger_cache = LedgerCache(args.ledger_cache.resolve() if args.ledger_cache else None)
    mark_phase("configuration")
    manifests = _discover_manifests(config["snapshot_roots"], args.snapshot_prefix, deep=args.deep_manifest_search)
    mark_phase("manifest_discovery")
    chains: dict[str, list[dict[str, Any]]] = {}
    snapshot_state: dict[str, dict[str, Any]] = {}
    manifest_inventory: list[dict[str, Any]] = []
    for manifest in manifests:
        records, inventory = _manifest_records(manifest, workspace, path_prefixes)
        manifest_inventory.append(inventory)
        _insert_snapshot_chains(chains, records, snapshot_state, args.max_chain)
    mark_phase("manifest_records")

    # Hash the bounded snapshot cohort before touching provider evidence.  When
    # every selected snapshot post-image is already byte-identical in the
    # worktree, a full ledger walk cannot improve the selected content for any
    # of those paths.  It can still reveal session-only paths or tombstones, so
    # auto mode records that coverage as deferred rather than claiming recovery
    # completeness.  --session-ledgers=always preserves the forensic path.
    initial_paths = set(chains)
    initial_worktree_records = _tree_records(
        workspace, "worktree", cache, workers, workspace,
        None if args.include_unreferenced_worktree else initial_paths,
    )
    for record in initial_worktree_records:
        chains.setdefault(record["path"], []).append(record)
    # Git trees are complete post-images too.  A bounded --path-prefix must
    # enumerate every tracked file below that prefix, including files deleted
    # from the worktree and absent from snapshots or session ledgers.  A
    # full-repo Git sweep stays explicit because it hashes every source blob.
    include_all_git = bool(args.include_unreferenced_worktree or args.include_unreferenced_git or path_prefixes)
    initial_git_records, initial_git_inventory = _git_head_records(
        workspace,
        None if include_all_git else initial_paths,
        path_prefixes=path_prefixes,
    )
    for record in initial_git_records:
        chains.setdefault(record["path"], []).append(record)
    fast_snapshot_observation = _snapshot_current_convergence(chains)
    mark_phase("snapshot_git_worktree_convergence")

    session_mode = args.session_ledgers
    defer_session_evidence = (
        session_mode == "auto"
        and not args.include_unreferenced_worktree
        and bool(fast_snapshot_observation["all_selected_snapshot_paths_match_worktree"])
    )
    if defer_session_evidence:
        session_inventory = [
            {"path": str(Path(raw)), "exists": Path(raw).exists(), "indexes": "not_scanned", "ledgers": "not_scanned"}
            for raw in config["session_roots"]
        ]
        ledgers: list[Path] = []
        ledger_session_ids: set[str] = set()
        session_discovery = {
            "mode": "deferred_fast_snapshot_converged",
            "reason": "Every selected complete snapshot path is byte-identical in the current worktree; session-only paths and tombstones remain unexamined.",
            "complete_file_recovery": "not_evaluated",
            **fast_snapshot_observation,
        }
        tombstone_inventory = {
            "enabled": bool(args.raw_tombstones),
            "state": "deferred_fast_snapshot_converged",
            "tombstones": 0,
            "reason": "Raw session traversal was deferred with ledger discovery; this is not evidence that no later deletion exists.",
        }
        mark_phase("session_ledger_discovery_deferred")
        mark_phase("session_ledger_records_deferred")
        mark_phase("raw_tombstones_deferred")
    elif session_mode == "never":
        session_inventory = [
            {"path": str(Path(raw)), "exists": Path(raw).exists(), "indexes": "not_scanned", "ledgers": "not_scanned"}
            for raw in config["session_roots"]
        ]
        ledgers = []
        ledger_session_ids = set()
        session_discovery = {
            "mode": "disabled_explicitly",
            "reason": "--session-ledgers=never was requested; session-only paths and tombstones are not evaluated.",
            "complete_file_recovery": "not_evaluated",
            **fast_snapshot_observation,
        }
        tombstone_inventory = {
            "enabled": bool(args.raw_tombstones),
            "state": "disabled_with_session_ledgers",
            "tombstones": 0,
            "reason": "Raw tombstones require session traversal and were disabled with --session-ledgers=never.",
        }
        mark_phase("session_ledger_discovery_disabled")
        mark_phase("session_ledger_records_disabled")
        mark_phase("raw_tombstones_disabled")
    else:
        session_inventory, ledgers = _discover_session_inventory(config["session_roots"])
        session_discovery = {
            "mode": "completed",
            "complete_file_recovery": "not_evaluated",
            **fast_snapshot_observation,
        }
        mark_phase("session_ledger_discovery")
        ledger_session_ids = set()
        for ledger in ledgers:
            for record in _cached_ledger_records(ledger, workspace, ledger_cache):
                if not _matches_path_prefix(record["path"], path_prefixes):
                    continue
                chains.setdefault(record["path"], []).append(record)
                source_id = str(record.get("source_id") or "")
                if source_id:
                    ledger_session_ids.add(source_id)
        mark_phase("session_ledger_records")
        if args.raw_tombstones:
            tombstones, tombstone_inventory = _session_delete_records(
                config["session_roots"], workspace, args.raw_tombstone_files, args.raw_tombstone_max_bytes, ledger_session_ids,
                tombstone_cache, args.raw_tombstone_include,
            )
            for record in tombstones:
                if not _matches_path_prefix(record["path"], path_prefixes):
                    continue
                chains.setdefault(record["path"], []).append(record)
        else:
            tombstone_inventory = {"enabled": False, "state": "disabled_explicitly", "tombstones": 0}
        mark_phase("raw_tombstones")

    referenced_paths = set(chains)
    if include_all_git:
        git_records, git_inventory = initial_git_records, initial_git_inventory
    else:
        additional_git_paths = referenced_paths - initial_paths
        if additional_git_paths:
            additional_git_records, additional_git_inventory = _git_head_records(workspace, additional_git_paths)
            git_records = initial_git_records + additional_git_records
            git_inventory = {
                **initial_git_inventory,
                "candidates": len(git_records),
                "path_filter_applied": True,
                "query_batches": [
                    {"paths": len(initial_paths), "candidates": len(initial_git_records)},
                    {"paths": len(additional_git_paths), "candidates": len(additional_git_records)},
                ],
            }
            for record in additional_git_records:
                chains.setdefault(record["path"], []).append(record)
        else:
            git_records, git_inventory = initial_git_records, {
                **initial_git_inventory,
                "query_batches": [{"paths": len(initial_paths), "candidates": len(initial_git_records)}],
            }
    mark_phase("git_head")

    candidate_inventory: list[dict[str, Any]] = []
    for item in config["candidate_roots"]:
        root = Path(item["path"])
        if root == workspace or not root.is_dir():
            candidate_inventory.append({**item, "exists": root.exists(), "candidates": 0})
            continue
        records = _tree_records(
            root,
            item["source"],
            cache,
            workers,
            workspace,
            None if args.include_unreferenced_worktree else set(chains),
        )
        candidate_inventory.append({**item, "exists": True, "candidates": len(records)})
        for record in records:
            chains.setdefault(record["path"], []).append(record)
    mark_phase("candidate_roots")

    worktree_filter = None if args.include_unreferenced_worktree else _expanded_worktree_paths(set(chains))
    if worktree_filter is None:
        worktree_records = initial_worktree_records
    else:
        # The initial bounded hash pass is reused; only session/candidate paths
        # and same-stem alternatives discovered afterwards need another stat.
        initial_record_paths = {str(record["path"]) for record in initial_worktree_records}
        additional_filter = worktree_filter - initial_record_paths
        additional_records = _tree_records(workspace, "worktree", cache, workers, workspace, additional_filter)
        worktree_records = initial_worktree_records + additional_records
    # Do not append a second worktree evidence row for the fast pass.
    initial_record_ids = {id(record) for record in initial_worktree_records}
    for record in worktree_records:
        if id(record) not in initial_record_ids:
            chains.setdefault(record["path"], []).append(record)
    mark_phase("worktree_records")

    case_aliases_merged = _merge_case_equivalent_chains(
        chains,
        [str(record["path"]) for record in git_records]
        + [str(record["path"]) for record in worktree_records],
    )

    queues: dict[str, list[dict[str, Any]]] = {
        "identical_converged": [],
        "safe_promote": [],
        "conflict_manual": [],
        "missing_source": [],
        "post_baseline_overlay": [],
        "intentionally_removed": [],
        "tombstone_conflict_manual": [],
        "awaiting_tombstone_scan": [],
        "module_shadowed_manual": [],
        "ephemeral_evidence": [],
    }
    tombstone_coverage = {
        "enabled": bool(tombstone_inventory.get("enabled")),
        "available": set(tombstone_inventory.get("available_priority_session_ids") or []),
        "scanned": set(tombstone_inventory.get("scanned_priority_session_ids") or []),
    }
    existing_paths = {str(record["path"]) for record in worktree_records}
    git_changed_paths = _git_changed_paths(workspace)
    for relative in sorted(chains, key=str.casefold):
        queue, record = _classify(
            relative, chains[relative], snapshot_state, tombstone_coverage, existing_paths, git_changed_paths,
            include_chain=args.include_chains,
        )
        queues[queue].append(record)
    mark_phase("classification")

    ended = dt.datetime.now(tz=dt.timezone.utc)
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "mode": "query-only",
        "production_files_written": False,
        "historical_commands_executed": False,
        "evidence_contract": EVIDENCE_CONTRACT,
        "workspace_root": str(workspace),
        "path_prefixes": list(path_prefixes),
        "created_at": ended.isoformat(),
        "elapsed_seconds": round((ended - started).total_seconds(), 3),
        "phase_seconds": phase_seconds,
        "resource_policy": {"workers": workers, "priority": priority, "tests_or_builds_started": False},
        "discovery_config": config,
        "inventories": {
            "snapshot_manifests": manifest_inventory,
            "git": git_inventory,
            "session_roots": session_inventory,
            "session_ledgers": [str(path) for path in ledgers],
            "session_ledger_discovery": session_discovery,
            "session_ledger_cache": {
                "hits": ledger_cache.hits,
                "misses": ledger_cache.misses,
                "configured": bool(ledger_cache.cache_path),
            },
            "session_tombstones": tombstone_inventory,
            "candidate_roots": candidate_inventory,
            "worktree_source_files": len(worktree_records),
            "git_worktree_comparison": {
                "available": git_changed_paths is not None,
                "changed_tracked_paths": len(git_changed_paths or ()),
                "rule": "Git clean-filter equivalence prevents CRLF/filter-only false conflicts.",
            },
            "path_identity": {
                "case_aliases_merged": case_aliases_merged,
                "rule": "Windows path aliases collapse onto Git/worktree canonical casing.",
            },
        },
        "hash_cache": {"hits": cache.hits, "misses": cache.misses, "persisted": bool(args.write_cache)},
        "queue_counts": {name: len(records) for name, records in queues.items()},
        "snapshot_convergence": {
            "paths_converged": sum(bool(state.get("converged")) for state in snapshot_state.values()),
            "older_candidates_skipped": sum(int(state.get("dropped", 0)) for state in snapshot_state.values()),
        },
        "queues": queues,
    }
    if args.out:
        frozen = _freeze_safe_promote_post_images(queues, args.out.resolve())
        result["inventories"]["frozen_post_images"] = frozen
    result["queue_counts"] = {name: len(records) for name, records in queues.items()}
    result["recovery_efficiency"] = _recovery_efficiency(
        manifest_inventory, queues, snapshot_state,
    )
    result["plan_sha256"] = _canonical_hash(result)
    if args.write_cache:
        if not args.hash_cache:
            raise SystemExit("--write-cache requires --hash-cache")
        cache.save()
    if args.write_tombstone_cache:
        if not args.tombstone_cache:
            raise SystemExit("--write-tombstone-cache requires --tombstone-cache")
        tombstone_cache.save()
    if args.write_ledger_cache:
        if not args.ledger_cache:
            raise SystemExit("--write-ledger-cache requires --ledger-cache")
        ledger_cache.save()
    if args.out:
        args.out.resolve().parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(args.out.resolve(), result)
        summary = {key: value for key, value in result.items() if key != "queues"}
        summary["plan_path"] = str(args.out.resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.summary_only:
        summary = {key: value for key, value in result.items() if key != "queues"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _freeze_safe_promote_post_images(
    queues: dict[str, list[dict[str, Any]]], plan_path: Path,
) -> dict[str, Any]:
    """Freeze mutable complete post-images beside an emitted immutable plan.

    Publish trees and session artifacts are useful discovery evidence but may be
    rebuilt or pruned between a scan and a guarded apply.  A safe-promote plan
    therefore owns a content-addressed evidence copy, never a mutable source
    path alone.  Git candidates already have an immutable object id and use
    ``git show`` during apply instead.
    """

    root = plan_path.parent / ".recovery-post-images"
    frozen = 0
    unavailable: list[dict[str, Any]] = []
    safe_promote = queues.get("safe_promote") or []
    retained: list[dict[str, Any]] = []
    for item in safe_promote:
        selected = item.get("selected") or {}
        if str(selected.get("source") or "") == "git-head":
            retained.append(item)
            continue
        expected = str(selected.get("hash") or "")
        artifact_value = selected.get("frozen_artifact") or selected.get("artifact")
        artifact = Path(str(artifact_value)).resolve() if artifact_value else None
        if not expected or not artifact or not artifact.is_file() or _sha256_file(artifact) != expected:
            item["reason"] = "candidate post-image was unavailable or changed before plan freeze"
            unavailable.append(item)
            continue
        target = root / expected[:2] / expected
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + ".", delete=False) as handle:
                temporary = Path(handle.name)
                with artifact.open("rb") as source:
                    shutil.copyfileobj(source, handle)
            if _sha256_file(temporary) != expected:
                temporary.unlink(missing_ok=True)
                item["reason"] = "candidate post-image changed while being frozen"
                unavailable.append(item)
                continue
            os.replace(temporary, target)
        if _sha256_file(target) != expected:
            raise SystemExit(f"recovery evidence integrity failure: {target}")
        selected["frozen_artifact"] = str(target.resolve())
        retained.append(item)
        frozen += 1
    if unavailable:
        queues["safe_promote"] = retained
        for item in unavailable:
            item["recovery_status"] = {
                "exact_complete_file_recovery": "not_recovered",
                "post_recovery_runnability": "not_evaluated",
            }
            queues["missing_source"].append(item)
    return {
        "root": str(root.resolve()),
        "frozen_candidates": frozen,
        "git_object_candidates": sum(
            1 for item in retained if str((item.get("selected") or {}).get("source") or "") == "git-head"
        ),
        "unavailable_before_freeze": len(unavailable),
        "rule": "safe-promote non-Git candidates are copied to content-addressed evidence before plan hashing",
    }


def _append_journal(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def apply_baselines(args: argparse.Namespace) -> int:
    plan_path = args.plan.resolve()
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid recovery plan: {exc}") from exc
    if plan.get("schema") != SCHEMA:
        raise SystemExit(f"unsupported recovery plan schema: {plan.get('schema')}")
    expected_plan_hash = str(plan.get("plan_sha256") or "")
    actual_plan_hash = _canonical_hash(plan)
    if not expected_plan_hash or expected_plan_hash != actual_plan_hash:
        raise SystemExit("apply refused: recovery plan hash does not verify")
    workspace = args.workspace_root.resolve()
    if str(workspace) != args.confirm_workspace or str(workspace) != str(Path(plan["workspace_root"]).resolve()):
        raise SystemExit("apply refused: --confirm-workspace and plan workspace must exactly match resolved workspace")
    prefixes = tuple(_normalized_prefix(value) for value in args.path_prefix)
    actions: list[dict[str, Any]] = []
    for item in plan.get("queues", {}).get("safe_promote", []):
        relative = str(item.get("path") or "")
        if prefixes and not any(_path_has_prefix(relative, prefix) for prefix in prefixes):
            continue
        target = (workspace / relative).resolve()
        if not _is_relative_to(target, workspace):
            raise SystemExit(f"apply refused: target escapes workspace: {relative}")
        selected = item.get("selected") or {}
        artifact_value = selected.get("frozen_artifact") or selected.get("artifact")
        artifact = Path(str(artifact_value)).resolve() if artifact_value else None
        source = str(selected.get("source") or "")
        actions.append({
            "path": relative,
            "target": str(target),
            "before_sha256": None,
            "candidate": str(artifact) if artifact else None,
            "candidate_sha256": selected.get("hash"),
            "candidate_source": source,
            "git_source_id": str(selected.get("source_id") or "") if source == "git-head" else None,
            "git_blob": str(selected.get("git_blob") or "") if source == "git-head" else None,
        })
    preview = {
        "mode": "apply" if args.apply else "dry-run",
        "plan": str(plan_path),
        "plan_sha256": actual_plan_hash,
        "workspace_root": str(workspace),
        "path_prefixes": list(prefixes),
        "safe_promote_actions": len(actions),
        "production_files_written": False,
        "actions": actions,
    }
    if not args.apply:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    cache = HashCache()
    git_post_images: dict[str, bytes] = {}
    for action in actions:
        target = Path(action["target"])
        artifact = Path(str(action["candidate"])) if action["candidate"] else None
        if target.exists():
            raise SystemExit(f"apply refused: safe-promote target now exists: {target}")
        if artifact and artifact.is_file():
            if cache.digest(artifact) != action["candidate_sha256"]:
                raise SystemExit(f"apply refused: candidate hash changed: {artifact}")
        elif action.get("candidate_source") == "git-head" and action.get("git_source_id") and action.get("git_blob"):
            body = _git_blob_bytes(workspace, str(action["git_source_id"]), str(action["path"]))
            if _sha256_bytes(body) != action["candidate_sha256"]:
                raise SystemExit(f"apply refused: Git post-image hash changed: {action['path']}")
            git_post_images[str(action["path"])] = body
        else:
            raise SystemExit(f"apply refused: candidate post-image missing: {artifact}")

    stamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = args.snapshot_root.resolve() / f"{stamp}-{actual_plan_hash[:12]}"
    run_root.mkdir(parents=True, exist_ok=False)
    snapshot = {
        "schema": "omnicompany.recovery.pre-apply-snapshot.v1",
        "created_at": _now_iso(),
        "plan": str(plan_path),
        "plan_sha256": actual_plan_hash,
        "workspace_root": str(workspace),
        "targets": [{**action, "before_state": "missing"} for action in actions],
    }
    snapshot["snapshot_sha256"] = _canonical_hash(snapshot)
    _atomic_json(run_root / "pre-apply-snapshot.json", snapshot)
    journal = run_root / "journal.jsonl"
    staged_root = run_root / "staged"
    applied = 0
    for action in actions:
        target = Path(action["target"])
        artifact = Path(str(action["candidate"])) if action["candidate"] else None
        staged = (staged_root / action["path"]).resolve()
        if not _is_relative_to(staged, staged_root.resolve()):
            raise SystemExit(f"apply refused: staged path escapes run root: {action['path']}")
        staged.parent.mkdir(parents=True, exist_ok=True)
        if artifact is not None:
            shutil.copyfile(artifact, staged)
        else:
            staged.write_bytes(git_post_images[str(action["path"])])
        if cache.digest(staged) != action["candidate_sha256"]:
            raise SystemExit(f"apply refused: staged candidate hash mismatch: {staged}")
        _append_journal(journal, {"event": "prepared", "at": _now_iso(), **action, "staged": str(staged)})
        if target.exists():
            _append_journal(journal, {"event": "refused", "at": _now_iso(), **action, "reason": "target appeared"})
            raise SystemExit(f"apply refused: target appeared after snapshot: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, target)
        actual = cache.digest(target)
        if actual != action["candidate_sha256"]:
            _append_journal(journal, {"event": "verify-failed", "at": _now_iso(), **action, "actual_sha256": actual})
            raise SystemExit(f"post-apply verification failed: {target}")
        _append_journal(journal, {"event": "applied", "at": _now_iso(), **action, "actual_sha256": actual})
        applied += 1
    preview.update({
        "production_files_written": bool(applied),
        "applied": applied,
        "pre_apply_snapshot": str(run_root / "pre-apply-snapshot.json"),
        "journal": str(journal),
    })
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    return 0


def _normalized_prefix(value: str) -> str:
    """Return a workspace-relative prefix suitable for an apply allow-list."""
    normalized = value.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized or ":" in normalized:
        raise SystemExit(f"invalid --path-prefix: {value!r}")
    return normalized


def _path_has_prefix(path: str, prefix: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return normalized == prefix or normalized.startswith(prefix + "/")


def _module_sibling(path: str, existing_paths: set[str]) -> str | None:
    suffix = Path(path).suffix.casefold()
    replacements = {
        ".ts": (".tsx",), ".tsx": (".ts",),
        ".js": (".jsx",), ".jsx": (".js",),
        ".mjs": (".mts",), ".mts": (".mjs",),
    }.get(suffix, ())
    stem = path[: -len(suffix)] if suffix else path
    return next((stem + candidate for candidate in replacements if stem + candidate in existing_paths), None)


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _provider_ids(provider_root: Path) -> list[str]:
    """Discover real provider implementations without trusting a stale registry."""
    if not provider_root.is_dir():
        return []
    discovered: list[str] = []
    for candidate in sorted(provider_root.iterdir(), key=lambda path: path.name):
        if not candidate.is_dir():
            continue
        names = (f"{candidate.name}.provider.ts", f"{candidate.name.replace('_', '-')}.provider.ts")
        if any((candidate / name).is_file() for name in names):
            discovered.append(candidate.name)
    return discovered


def verify_provider_closure(args: argparse.Namespace) -> int:
    """Verify post-recovery ChatUI provider integration only.

    This query never selects, orders, replaces, or certifies a recovery
    candidate.  Complete-file recovery is determined only from its write
    evidence and timestamp chain.  A closure result is a separate, current-tree
    runnability observation: an exact restoration can legitimately fail it,
    including when the original latest source was incomplete or its test suite
    is obsolete.
    """
    workspace = args.workspace_root.resolve()
    chatui = workspace / "src/omnicompany/dashboard/chatui"
    provider_root = chatui / "server/modules/providers/list"
    providers = _provider_ids(provider_root)
    targets = {
        "server_type": chatui / "server/shared/types.ts",
        "frontend_type": chatui / "src/types/app.ts",
        "registry": chatui / "server/modules/providers/provider.registry.ts",
        "routes": chatui / "server/modules/providers/provider.routes.ts",
        "capabilities": chatui / "server/modules/providers/services/provider-capabilities.service.ts",
        "synchronizer": chatui / "server/modules/providers/services/session-synchronizer.service.ts",
        "logo": chatui / "src/components/llm-logo-provider/SessionProviderLogo.tsx",
    }
    content = {name: _read_utf8(path) for name, path in targets.items()}
    missing: list[dict[str, str]] = []
    checks = {
        "server_type": lambda provider: f"'{provider}'" in content["server_type"],
        "frontend_type": lambda provider: f"'{provider}'" in content["frontend_type"],
        "registry": lambda provider: bool(re.search(rf"(?m)^\s+{re.escape(provider)}:\s+new\s+", content["registry"])),
        "routes": lambda provider: f"normalized === '{provider}'" in content["routes"],
        "capabilities": lambda provider: bool(re.search(rf"(?m)^\s+{re.escape(provider)}:\s*\{{", content["capabilities"])),
        "synchronizer": lambda provider: bool(re.search(rf"(?m)^\s+{re.escape(provider)}:\s*0,", content["synchronizer"])),
        # Claude is the deliberate default branch; every other provider needs
        # an explicit branch so a restored provider cannot silently render as
        # Claude in the selector.
        "logo": lambda provider: (
            "return <ClaudeLogo" in content["logo"]
            if provider == "claude"
            else f"provider === '{provider}'" in content["logo"]
        ),
    }
    for provider in providers:
        for name, check in checks.items():
            if not check(provider):
                missing.append({"provider": provider, "integration_point": name, "path": str(targets[name])})

    typecheck: dict[str, Any] | None = None
    if args.typecheck:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        result = subprocess.run(
            [npm, "run", "typecheck"], cwd=chatui, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", check=False,
        )
        typecheck = {"returncode": result.returncode, "output_tail": result.stdout[-4000:]}

    closure_passed = not missing and (typecheck is None or typecheck["returncode"] == 0)
    payload = {
        "schema": "omnicompany.recovery.post-recovery-provider-closure.v1",
        "mode": "query-only",
        "evidence_classification": {
            "complete_file_recovery": "not_evaluated",
            "post_recovery_runnability": "passed" if closure_passed else "failed",
        },
        "recovery_selection": {
            "status": "not_evaluated",
            "reason": "Use baseline scan and its per-path write-evidence chain to select the latest complete source file.",
        },
        "workspace_root": str(workspace),
        "providers": providers,
        "missing": missing,
        "typecheck": typecheck,
        "closure_passed": closure_passed,
        "automatic_scope": [
            "post-recovery provider implementation-to-integration closure",
            "optional current-tree TypeScript compile observation",
        ],
        "manual_observation_required": [
            "visual layout and interaction quality cannot be inferred from source closure",
            "real credential login, remote gateway state, and device pairing require authorized live observation",
        ],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if payload["closure_passed"] else 1


def add_baseline_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    baseline = subparsers.add_parser(
        "baseline",
        help="Automatically discover complete baselines and build per-path candidate chains.",
    )
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    scan = baseline_sub.add_parser(
        "scan",
        help="Query-only scan of snapshots, Git, worktree, publish/build roots, and bounded session evidence.",
    )
    scan.add_argument("--workspace-root", type=Path, required=True)
    scan.add_argument("--config", type=Path)
    scan.add_argument("--snapshot-root", type=Path, action="append", default=[])
    scan.add_argument("--snapshot-prefix", help="Limit proof/audit to automatically found snapshot ids with this prefix.")
    scan.add_argument(
        "--deep-manifest-search", action="store_true",
        help="Opt in to recursive manifest discovery even when --snapshot-prefix is supplied.",
    )
    scan.add_argument("--session-root", type=Path, action="append", default=[])
    scan.add_argument(
        "--session-ledgers", choices=("auto", "always", "never"), default="auto",
        help=(
            "auto defers ledger and raw-session traversal only when every selected complete snapshot path "
            "is byte-identical in the worktree; deferred session-only paths/tombstones are not treated as recovered. "
            "Use always for forensic coverage or never to explicitly disable session evidence."
        ),
    )
    scan.add_argument("--candidate-root", type=Path, action="append", default=[])
    scan.add_argument(
        "--path-prefix", action="append", default=[],
        help="Restrict discovery to one or more workspace-relative paths; use this for bounded per-project recovery batches.",
    )
    scan.add_argument(
        "--discover-candidate-roots", action="store_true",
        help="Opt in to a depth-limited workspace search for nested build/dist trees; default checks only named roots and publish trees.",
    )
    scan.add_argument("--max-chain", type=int, default=6)
    scan.add_argument(
        "--include-chains", action="store_true",
        help="Include every candidate chain in the plan for forensic review; default keeps only the selected, baseline, and tombstone evidence needed for guarded apply.",
    )
    scan.add_argument("--workers", type=int, default=2, help="Clamped to the safe range 1..2.")
    scan.add_argument("--normal-priority", action="store_true", help="Do not lower this process priority.")
    scan.add_argument(
        "--include-unreferenced-worktree",
        action="store_true",
        help="Opt in to expensive full Git/worktree/publish enumeration; default hashes referenced paths only.",
    )
    scan.add_argument(
        "--include-unreferenced-git",
        action="store_true",
        help="Enumerate all tracked Git source blobs to find deletions absent from snapshots/ledgers; prefer --path-prefix for a bounded project sweep.",
    )
    scan.add_argument("--hash-cache", type=Path, help="Optional persistent hash cache to read.")
    scan.add_argument("--write-cache", action="store_true", help="Explicitly persist --hash-cache after the scan.")
    scan.add_argument("--tombstone-cache", type=Path, help="Optional path/stat cache for bounded raw deletion extraction.")
    scan.add_argument("--write-tombstone-cache", action="store_true", help="Explicitly persist --tombstone-cache after the scan.")
    scan.add_argument("--ledger-cache", type=Path, help="Optional path/stat cache for complete-file session ledgers.")
    scan.add_argument("--write-ledger-cache", action="store_true", help="Explicitly persist --ledger-cache after the scan.")
    scan.add_argument(
        "--raw-tombstones", action=argparse.BooleanOptionalAction, default=True,
        help="Boundedly inspect recent raw provider transcripts for completed apply_patch deletions (default: enabled).",
    )
    scan.add_argument(
        "--raw-tombstone-include", type=Path, action="append", default=[],
        help="Also inspect these exact raw .jsonl transcripts for tombstones; they are added to, never replace, the bounded recent window.",
    )
    scan.add_argument("--raw-tombstone-files", type=int, default=8, help="Maximum recent, ledger-prioritized raw session files to inspect.")
    scan.add_argument("--raw-tombstone-max-bytes", type=int, default=100 * 1024 * 1024, help="Per-session byte ceiling for tombstone extraction.")
    scan.add_argument("--out", type=Path, help="Write the immutable plan/report outside production paths.")
    scan.add_argument("--progress-file", type=Path, help="Optional evidence-only atomic progress file, updated after each discovery phase.")
    scan.add_argument("--summary-only", action="store_true")
    scan.set_defaults(func=discover_baselines)

    closure = baseline_sub.add_parser(
        "closure",
        help="Query-only post-recovery provider integration checks; never selects recovery candidates.",
    )
    closure.add_argument("--workspace-root", type=Path, required=True)
    closure.add_argument("--typecheck", action="store_true", help="Also run the ChatUI TypeScript closure check.")
    closure.add_argument("--json-out", type=Path, help="Optional evidence report path outside production sources.")
    closure.set_defaults(func=verify_provider_closure)

    apply = baseline_sub.add_parser(
        "apply",
        help="Dry-run by default; apply only frozen safe-promote missing files with guards and journal.",
    )
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--workspace-root", type=Path, required=True)
    apply.add_argument("--confirm-workspace", default="")
    apply.add_argument(
        "--path-prefix",
        action="append",
        default=[],
        help="Repeatable workspace-relative allow-list; omit only to include every safe-promote file.",
    )
    apply.add_argument(
        "--snapshot-root", type=Path,
        default=Path("C:/workspace/omnicompany-recovery-evidence/apply-snapshots"),
    )
    apply.add_argument("--apply", action="store_true")
    apply.set_defaults(func=apply_baselines)


if __name__ == "__main__":
    raise SystemExit(
        "recovery_baseline_cli is an extension module; invoke "
        "scripts/session_recovery_cli.py baseline scan|apply instead"
    )
