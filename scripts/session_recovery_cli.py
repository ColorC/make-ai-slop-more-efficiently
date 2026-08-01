#!/usr/bin/env python3
# OMNI-PERSISTENT-SCRIPT
# owner: Omnicompany recovery
# purpose: archive, query, and safely materialize session-derived file evidence.
"""Safe front door for archiving and querying local AI session evidence.

Phase A intentionally does not overwrite existing workspace files.  Historical
commands are data: this tool never executes a command recovered from a session.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Iterable

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_PACKAGE = _SOURCE_ROOT / "src"
if _SOURCE_PACKAGE.is_dir() and str(_SOURCE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_SOURCE_PACKAGE))

from omnicompany.packages.services.recovery.providers import source_specs as recovery_source_specs
from omnicompany.packages.services.recovery.commands import add_portable_parsers

try:  # Installed wheel: both compatibility modules live in the same package.
    from .recovery_baseline_cli import add_baseline_parser
except ImportError:  # Source checkout: ``python scripts/recover.py``.
    script_directory = str(Path(__file__).resolve().parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    from recovery_baseline_cli import add_baseline_parser


SCHEMA_VERSION = 1
DEFAULT_RECOVERY_HOME = Path(os.path.expandvars(os.path.expanduser(
    os.environ.get("OMNI_RECOVERY_HOME", "~/.omni-recover")
))).resolve()
DEFAULT_ARCHIVE = DEFAULT_RECOVERY_HOME / "session-archive"
DEFAULT_ENGINE = Path(os.environ.get(
    "OMNI_RECOVERY_LEGACY_ENGINE",
    "C:/workspace/omnicompany-recovery-evidence/"
    "20260801-reconstruction-facility/session_forensic_timeline.py",
))
DEFAULT_LEDGER_ROOT = DEFAULT_RECOVERY_HOME
OPENCODE_EVIDENCE_TOOLS = ("write", "edit", "apply_patch", "bash", "read")


def _terminal_write_shape(command: object) -> str | None:
    """Classify a terminal write *shape* without interpreting or executing it.

    A command transcript is never a post-image by itself.  This deliberately
    narrow recognizer exists so that PowerShell/Bash-originated writes are
    visible in the recovery ledger, rather than being silently grouped with
    ordinary command leads.  It does not parse substitutions, expand globs,
    or claim that the command actually succeeded.
    """

    if not isinstance(command, str):
        return None
    lowered = command.casefold()
    if re.search(r"\b(?:set-content|out-file|add-content)\b", lowered):
        return "powershell-content-cmdlet"
    if re.search(r"(?:^|[\s;&|])(?:cat|tee)\b.*(?:<<|>)|(?:^|[\s;&|])(?:echo|printf)\b.*>", lowered):
        return "shell-redirection-or-heredoc"
    if re.search(r"\b(?:write_text|write_bytes|writefile|write-file)\s*\(", lowered):
        return "programmatic-file-write"
    return None


def _replay_disposition(candidate_kind: str, evidence_class: str) -> dict[str, object]:
    """Keep source replay certainty separate from runnability and intent.

    The values model the *editing mechanism*, not the apparent meaning of a
    file or whether a contemporary test happened to pass.  Callers must still
    establish timestamp ordering and pre-image hashes before applying anything.
    """

    if candidate_kind == "complete-file":
        return {
            "source_replay_state": "eligible_exact_post_image",
            "automatic_apply_eligible": True,
            "preconditions": ["completed tool result", "path and full post-image are retained", "frozen hash-guarded plan"],
        }
    if candidate_kind in {"hunk-requires-baseline", "patch-requires-baseline"}:
        return {
            "source_replay_state": "conditionally_deterministic_hunk",
            "automatic_apply_eligible": False,
            "preconditions": ["exact pre-image SHA-256", "unambiguous chronological order", "all hunks apply byte-for-byte", "frozen hash-guarded plan"],
        }
    if candidate_kind == "terminal-write-requires-post-image":
        return {
            "source_replay_state": "terminal_write_needs_materialized_post_image",
            "automatic_apply_eligible": False,
            "preconditions": ["capture a later complete file artifact or Git/snapshot blob", "never rerun historical command"],
        }
    return {
        "source_replay_state": "not_replayable_from_this_record",
        "automatic_apply_eligible": False,
        "preconditions": ["corroborate with an independent complete post-image"],
    }


def expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


SOURCE_SPECS = recovery_source_specs()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matches(path: str, patterns: Iterable[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def iter_source_files(provider: str) -> Iterable[tuple[Path, Path, str]]:
    spec = SOURCE_SPECS[provider]
    seen: set[tuple[int, int] | str] = set()
    for raw_root in spec["roots"]:
        root = expand(raw_root)
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if not matches(rel, spec["includes"]) or matches(rel, spec["excludes"]):
                continue
            try:
                stat = path.stat()
                identity: tuple[int, int] | str = (stat.st_dev, stat.st_ino)
            except OSError:
                identity = str(path).lower()
            if identity in seen:
                continue
            seen.add(identity)
            yield root, path, rel


def source_report() -> dict:
    providers = []
    for provider, spec in SOURCE_SPECS.items():
        roots = []
        for raw in spec["roots"]:
            path = expand(raw)
            roots.append({"path": str(path), "exists": path.exists(), "is_dir": path.is_dir()})
        providers.append({
            "provider": provider,
            "roots": roots,
            "includes": spec["includes"],
            "excludes": spec["excludes"],
        })
    return {"schema_version": SCHEMA_VERSION, "providers": providers}


def consistent_bytes(path: Path) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        return path, None
    holder = tempfile.TemporaryDirectory(prefix="omni-session-sqlite-")
    snapshot = Path(holder.name) / path.name
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(snapshot)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return snapshot, holder


def archive_create(args: argparse.Namespace) -> int:
    providers = args.provider or list(SOURCE_SPECS)
    unknown = sorted(set(providers) - set(SOURCE_SPECS))
    if unknown:
        raise SystemExit(f"unknown providers: {', '.join(unknown)}")
    cutoff = dt.datetime.fromisoformat(args.since).timestamp() if args.since else None
    selected: list[tuple[str, Path, Path, str]] = []
    for provider in providers:
        for root, path, rel in iter_source_files(provider):
            if cutoff is not None and path.stat().st_mtime < cutoff:
                continue
            selected.append((provider, root, path, rel))
    selected.sort(key=lambda row: (row[0], str(row[2]).lower()))
    estimate = {
        "schema_version": SCHEMA_VERSION,
        "mode": "dry-run" if args.dry_run else "archive",
        "files": len(selected),
        "bytes": sum(path.stat().st_size for _, _, path, _ in selected),
        "providers": providers,
    }
    if args.dry_run:
        print(json.dumps(estimate, ensure_ascii=False, indent=2))
        return 0

    archive_root = args.archive_root.resolve()
    objects = archive_root / "objects" / "sha256"
    manifests = archive_root / "manifests"
    objects.mkdir(parents=True, exist_ok=True)
    manifests.mkdir(parents=True, exist_ok=True)
    records = []
    for provider, root, source_path, rel in selected:
        materialized, holder = consistent_bytes(source_path)
        try:
            digest = sha256_file(materialized)
            destination = objects / digest[:2] / digest
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if sha256_file(destination) != digest:
                    raise RuntimeError(f"archive object corruption: {destination}")
            else:
                temporary = destination.with_suffix(".partial")
                shutil.copyfile(materialized, temporary)
                if sha256_file(temporary) != digest:
                    temporary.unlink(missing_ok=True)
                    raise RuntimeError(f"copy verification failed: {source_path}")
                os.replace(temporary, destination)
            stat = source_path.stat()
            records.append({
                "provider": provider,
                "root": str(root),
                "relative_path": rel,
                "source_path": str(source_path),
                "source_size": stat.st_size,
                "source_mtime_ns": stat.st_mtime_ns,
                "blob_sha256": digest,
                "archived_size": destination.stat().st_size,
                "sqlite_consistent_snapshot": holder is not None,
            })
        finally:
            if holder is not None:
                holder.cleanup()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        **estimate,
        "mode": "archive",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "complete": True,
        "records": records,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payload["records_sha256"] = hashlib.sha256(
        json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest = manifests / f"archive-{stamp}.json"
    temporary = manifest.with_suffix(".partial")
    temporary.write_bytes(encoded)
    os.replace(temporary, manifest)
    print(json.dumps({**estimate, "manifest": str(manifest), "manifest_sha256": sha256_file(manifest)}, ensure_ascii=False, indent=2))
    return 0


def archive_verify(args: argparse.Namespace) -> int:
    manifest = args.manifest.resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if args.sample and args.sample < len(records):
        stride = max(1, len(records) // args.sample)
        records = records[::stride][: args.sample]
    root = manifest.parent.parent
    failures = []
    for record in records:
        digest = record["blob_sha256"]
        path = root / "objects" / "sha256" / digest[:2] / digest
        if not path.is_file() or sha256_file(path) != digest:
            failures.append(str(path))
    result = {"manifest": str(manifest), "checked": len(records), "failures": failures, "ok": not failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _opencode_connection(database: Path) -> sqlite3.Connection:
    """Open an OpenCode database without granting this process write access."""

    database = database.expanduser().resolve()
    if not database.is_file():
        raise SystemExit(f"OpenCode database not found: {database}")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name IN ('session', 'part')"
        )
    }
    if tables != {"session", "part"}:
        connection.close()
        raise SystemExit("unsupported OpenCode database: session/part tables are required")
    return connection


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _first_string(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            return value
    return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text_descriptor(value: object, include_payloads: bool) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    result: dict[str, Any] = {
        "utf8_bytes": len(value.encode("utf-8")),
        "sha256": _sha256_text(value),
    }
    if include_payloads:
        result["text"] = value
    else:
        result["excerpt"] = value[:240]
    return result


def _patch_targets(patch_text: object) -> list[str]:
    if not isinstance(patch_text, str):
        return []
    targets: list[str] = []
    prefixes = (
        "*** Add File:",
        "*** Update File:",
        "*** Delete File:",
        "*** Move to:",
    )
    for line in patch_text.splitlines():
        stripped = line.strip()
        for prefix in prefixes:
            if stripped.startswith(prefix):
                target = stripped[len(prefix):].strip()
                if target and target not in targets:
                    targets.append(target)
                break
    return targets


def _normalized_timestamp(milliseconds: int) -> str:
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc).isoformat()


def _opencode_record(
    row: sqlite3.Row,
    *,
    database: Path,
    include_payloads: bool,
) -> dict[str, Any] | None:
    try:
        data = json.loads(row["data"])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("type") != "tool":
        return None
    tool = str(data.get("tool") or data.get("name") or "").lower()
    if tool not in OPENCODE_EVIDENCE_TOOLS:
        return None
    state = _json_object(data.get("state"))
    tool_input = _json_object(state.get("input"))
    metadata = _json_object(state.get("metadata"))
    status = str(state.get("status") or "unknown").lower()
    timing = _json_object(state.get("time"))
    event_ms = timing.get("end") if status == "completed" else timing.get("start")
    if not isinstance(event_ms, int):
        event_ms = int(row["time_updated"] or row["time_created"])

    direct_path = _first_string(tool_input, "filePath", "filepath", "file_path", "path")
    targets = [direct_path] if direct_path else []
    evidence_class = "unknown-tool-envelope"
    candidate_kind = "lead-only"
    payload: dict[str, Any] = {}
    if tool == "write":
        content_present = "content" in tool_input and isinstance(tool_input.get("content"), str)
        if status == "completed" and content_present and direct_path:
            evidence_class = "complete-mutation-post-image"
            candidate_kind = "complete-file"
        else:
            evidence_class = "incomplete-write-intent"
        payload["content"] = _text_descriptor(tool_input.get("content"), include_payloads)
    elif tool == "edit":
        evidence_class = "exact-edit-hunk" if status == "completed" else "incomplete-edit-intent"
        candidate_kind = "hunk-requires-baseline"
        payload["old_string"] = _text_descriptor(
            tool_input.get("oldString", tool_input.get("old_string")), include_payloads
        )
        payload["new_string"] = _text_descriptor(
            tool_input.get("newString", tool_input.get("new_string")), include_payloads
        )
    elif tool == "apply_patch":
        patch_text = tool_input.get("patchText", tool_input.get("patch"))
        targets = _patch_targets(patch_text)
        evidence_class = "exact-patch" if status == "completed" else "incomplete-patch-intent"
        candidate_kind = "patch-requires-baseline"
        payload["patch"] = _text_descriptor(patch_text, include_payloads)
    elif tool == "bash":
        command = tool_input.get("command", tool_input.get("cmd"))
        write_shape = _terminal_write_shape(command)
        evidence_class = "terminal-write-intent" if write_shape else "shell-command-intent"
        candidate_kind = "terminal-write-requires-post-image" if write_shape else "lead-only"
        payload["command"] = _text_descriptor(command, include_payloads)
        if write_shape:
            payload["terminal_write_shape"] = write_shape
        workdir = _first_string(tool_input, "workdir", "cwd")
        if workdir:
            payload["workdir"] = workdir
    elif tool == "read":
        evidence_class = "read-corroboration"
        candidate_kind = "corroboration-only"
        payload["output"] = _text_descriptor(state.get("output"), include_payloads)
        payload["truncated"] = bool(metadata.get("truncated", False))

    # An Edit/Patch or shell command is useful evidence, but it is deliberately
    # not promoted to a complete file without an exact later post-image.
    return {
        "provider": "opencode",
        "evidence_id": row["part_id"],
        "session_id": row["session_id"],
        "message_id": row["message_id"],
        "timestamp_ms": event_ms,
        "timestamp_utc": _normalized_timestamp(event_ms),
        "tool": tool,
        "status": status,
        "evidence_class": evidence_class,
        "candidate_kind": candidate_kind,
        "complete_post_image": candidate_kind == "complete-file",
        "replay_disposition": _replay_disposition(candidate_kind, evidence_class),
        "runnability_evidence": "not_evaluated",
        "target_paths": targets,
        "session_directory": row["directory"],
        "session_title": row["title"],
        "payload": payload,
        "source_pointer": {
            "database": str(database),
            "table": "part",
            "primary_key": row["part_id"],
        },
    }


def opencode_query_records(
    database: Path,
    *,
    tools: Iterable[str] = OPENCODE_EVIDENCE_TOOLS,
    scopes: Iterable[str] = (),
    since: str | None = None,
    limit: int = 100,
    include_payloads: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Return newest-first recovery evidence while leaving the source DB untouched."""

    if limit < 1:
        raise SystemExit("--limit must be at least 1")
    selected_tools = tuple(dict.fromkeys(str(tool).lower() for tool in tools))
    if not selected_tools:
        raise SystemExit("at least one OpenCode tool must be selected")
    unknown = sorted(set(selected_tools) - set(OPENCODE_EVIDENCE_TOOLS))
    if unknown:
        raise SystemExit(f"unsupported OpenCode tools: {', '.join(unknown)}")
    since_ms: int | None = None
    if since:
        parsed = dt.datetime.fromisoformat(since.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        since_ms = int(parsed.timestamp() * 1000)
    normalized_scopes = tuple(scope.casefold() for scope in scopes if scope)
    database = database.expanduser().resolve()
    connection = _opencode_connection(database)
    try:
        invalid_count = int(
            connection.execute("SELECT count(*) FROM part WHERE json_valid(data) = 0").fetchone()[0]
        )
        placeholders = ",".join("?" for _ in selected_tools)
        clauses = [
            "json_valid(p.data) = 1",
            "json_extract(p.data, '$.type') = 'tool'",
            f"lower(coalesce(json_extract(p.data, '$.tool'), json_extract(p.data, '$.name'))) IN ({placeholders})",
        ]
        parameters: list[object] = list(selected_tools)
        if since_ms is not None:
            clauses.append(
                "coalesce(json_extract(p.data, '$.state.time.end'), "
                "json_extract(p.data, '$.state.time.start'), p.time_updated, p.time_created) >= ?"
            )
            parameters.append(since_ms)
        query = f"""
            SELECT p.id AS part_id, p.message_id, p.session_id,
                   p.time_created, p.time_updated, p.data,
                   s.directory, s.title
              FROM part AS p
              JOIN session AS s ON s.id = p.session_id
             WHERE {' AND '.join(clauses)}
             ORDER BY coalesce(json_extract(p.data, '$.state.time.end'),
                               json_extract(p.data, '$.state.time.start'),
                               p.time_updated, p.time_created) DESC,
                      p.id DESC
        """
        records: list[dict[str, Any]] = []
        for row in connection.execute(query, parameters):
            record = _opencode_record(row, database=database, include_payloads=include_payloads)
            if record is None:
                continue
            if normalized_scopes:
                searchable = "\n".join(
                    [
                        *record["target_paths"],
                        str(record["session_directory"] or ""),
                        str(record["session_title"] or ""),
                        # Scope against the unabridged input envelope even when
                        # output payloads are redacted to hashes and excerpts.
                        str(row["data"]),
                    ]
                ).casefold()
                if not any(scope in searchable for scope in normalized_scopes):
                    continue
            records.append(record)
            if len(records) >= limit:
                break
        return records, invalid_count
    finally:
        connection.close()


def opencode_query(args: argparse.Namespace) -> int:
    records, invalid_count = opencode_query_records(
        args.database,
        tools=args.tool or OPENCODE_EVIDENCE_TOOLS,
        scopes=args.scope,
        since=args.since,
        limit=args.limit,
        include_payloads=args.include_payloads,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "provider": "opencode",
        "mode": "dry-run" if args.dry_run else "query-only",
        "source_database": str(args.database.expanduser().resolve()),
        "source_opened_read_only": True,
        "historical_commands_executed": False,
        "invalid_json_parts_quarantined": invalid_count,
        "filters": {
            "tools": args.tool or list(OPENCODE_EVIDENCE_TOOLS),
            "scopes": args.scope,
            "since": args.since,
            "limit": args.limit,
        },
        "evidence_count": len(records),
        "complete_post_images": sum(record["complete_post_image"] for record in records),
        "records": records,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _normalized_query_path(value: str) -> str:
    return value.replace("\\", "/").casefold().strip()


def _ledger_files(ledger_root: Path, explicit: Iterable[Path]) -> list[Path]:
    selected = [path.expanduser().resolve() for path in explicit]
    if not selected:
        root = ledger_root.expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"ledger root not found: {root}")
        selected = sorted(root.rglob("LATEST-COMPLETE-FILES.json"))
    missing = [str(path) for path in selected if not path.is_file()]
    if missing:
        raise SystemExit(f"ledger files not found: {', '.join(missing)}")
    return selected


def query_path_records(
    *,
    targets: Iterable[str],
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
    ledgers: Iterable[Path] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Query materialized latest-complete ledgers without touching worktrees."""

    normalized_targets = tuple(
        dict.fromkeys(_normalized_query_path(target) for target in targets if target.strip())
    )
    if not normalized_targets:
        raise SystemExit("at least one --target is required")
    ledger_paths = _ledger_files(ledger_root, ledgers)
    matches_by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ledger in ledger_paths:
        try:
            payload = json.loads(ledger.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid latest-complete ledger {ledger}: {exc}") from exc
        for record in payload.get("files", []):
            if not isinstance(record, dict):
                continue
            raw_path = str(record.get("path") or "")
            normalized_path = _normalized_query_path(
                str(record.get("normalized_path") or raw_path)
            )
            if not any(target in normalized_path for target in normalized_targets):
                continue
            candidate_artifact = record.get("candidate_artifact")
            candidate = Path(candidate_artifact).resolve() if candidate_artifact else None
            current = Path(raw_path)
            current_exists = current.is_file()
            current_sha256 = sha256_file(current) if current_exists else None
            candidate_exists = bool(candidate and candidate.is_file())
            candidate_sha256 = sha256_file(candidate) if candidate_exists and candidate else None
            expected_sha256 = record.get("candidate_sha256") or record.get("sha256")
            exact_current_match = bool(
                current_sha256
                and (candidate_sha256 or expected_sha256)
                and current_sha256 == (candidate_sha256 or expected_sha256)
            )
            result = {
                "path": raw_path,
                "normalized_path": normalized_path,
                "timestamp": record.get("timestamp"),
                "source": record.get("source"),
                "session_id": record.get("session_id"),
                "operation": record.get("operation"),
                "decision_at_index_time": record.get("decision"),
                "candidate_artifact": str(candidate) if candidate else None,
                "candidate_exists": candidate_exists,
                "candidate_sha256": candidate_sha256 or expected_sha256,
                "current_exists": current_exists,
                "current_sha256": current_sha256,
                "exact_current_match": exact_current_match,
                "later_hunk_count": len(record.get("later_hunks") or []),
                "delete_move_evidence_count": len(record.get("delete_move_evidence") or []),
                "ledger": str(ledger),
            }
            identity = (
                normalized_path,
                str(result["timestamp"] or ""),
                str(result["candidate_sha256"] or ""),
            )
            matches_by_identity[identity] = result
    records = sorted(
        matches_by_identity.values(),
        key=lambda record: (
            str(record.get("timestamp") or ""),
            str(record.get("path") or "").casefold(),
        ),
        reverse=True,
    )
    return records, [str(path) for path in ledger_paths]


def query_path(args: argparse.Namespace) -> int:
    records, ledgers = query_path_records(
        targets=args.target,
        ledger_root=args.ledger_root,
        ledgers=args.ledger,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "mode": "dry-run" if args.dry_run else "query-only",
        "historical_commands_executed": False,
        "production_files_written": False,
        "targets": args.target,
        "ledger_count": len(ledgers),
        "match_count": len(records),
        "records": records[: args.limit],
        "truncated": len(records) > args.limit,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _git_read(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Run one Git object/ref query with optional locking disabled."""

    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )


def _git_text(repo: Path, *arguments: str) -> str | None:
    completed = _git_read(repo, *arguments)
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _git_target(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"invalid Git target path: {value!r}")
    return path.as_posix()


def _git_ref_tips(repo: Path, include_reflog: bool) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []

    def append(name: str, kind: str, revision: str) -> None:
        commit = _git_text(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
        if commit:
            refs.append({"name": name, "kind": kind, "commit": commit})

    append("HEAD", "head", "HEAD")
    completed = _git_read(
        repo,
        "for-each-ref",
        "--format=%(refname)%00",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
        "refs/stash",
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"cannot enumerate Git refs in {repo}: "
            + completed.stderr.decode("utf-8", errors="replace").strip()
        )
    for raw_line in completed.stdout.splitlines():
        ref = raw_line.rstrip(b"\x00").decode("utf-8", errors="replace")
        if not ref:
            continue
        if ref == "refs/stash":
            kind = "stash"
        elif ref.startswith("refs/heads/"):
            kind = "head"
        elif ref.startswith("refs/remotes/"):
            kind = "remote"
        elif ref.startswith("refs/tags/"):
            kind = "tag"
        else:
            continue
        append(ref, kind, ref)
    if include_reflog:
        reflog = _git_read(repo, "reflog", "show", "--all", "--format=%H%x00%gD")
        if reflog.returncode == 0:
            for raw_line in reflog.stdout.splitlines():
                commit_raw, separator, selector_raw = raw_line.partition(b"\x00")
                if not separator:
                    continue
                commit = commit_raw.decode("ascii", errors="ignore")
                selector = selector_raw.decode("utf-8", errors="replace")
                if commit:
                    refs.append(
                        {
                            "name": f"reflog:{selector}",
                            "kind": "reflog",
                            "commit": commit,
                        }
                    )
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for ref in refs:
        unique[(ref["name"], ref["kind"], ref["commit"])] = ref
    return list(unique.values())


def _git_blob_versions(
    repo: Path,
    target: str,
    refs: list[dict[str, str]],
) -> list[dict[str, Any]]:
    history_cache: dict[str, list[str]] = {}
    object_cache: dict[str, tuple[str, bytes] | None] = {}
    versions: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in refs:
        tip = ref["commit"]
        commits = history_cache.get(tip)
        if commits is None:
            history = _git_read(repo, "rev-list", "--full-history", tip, "--", target)
            discovered = (
                history.stdout.decode("ascii", errors="ignore").splitlines()
                if history.returncode == 0
                else []
            )
            commits = list(dict.fromkeys([tip, *discovered]))
            history_cache[tip] = commits
        for commit in commits:
            cache_key = f"{commit}:{target}"
            if cache_key not in object_cache:
                object_id = _git_text(repo, "rev-parse", "--verify", cache_key)
                if not object_id or _git_text(repo, "cat-file", "-t", object_id) != "blob":
                    object_cache[cache_key] = None
                else:
                    blob = _git_read(repo, "cat-file", "blob", object_id)
                    object_cache[cache_key] = (
                        object_id,
                        blob.stdout,
                    ) if blob.returncode == 0 else None
            materialized = object_cache[cache_key]
            if materialized is None:
                continue
            object_id, content = materialized
            digest = hashlib.sha256(content).hexdigest()
            version = versions.setdefault(
                (target, digest),
                {
                    "target": target,
                    "blob_sha256": digest,
                    "bytes": len(content),
                    "git_object_ids": set(),
                    "refs": set(),
                    "commits": set(),
                },
            )
            version["git_object_ids"].add(object_id)
            version["refs"].add((ref["name"], ref["kind"]))
            version["commits"].add(commit)
    return list(versions.values())


def query_git_records(
    *,
    repo: Path,
    targets: Iterable[str],
    mirrors: Iterable[Path] = (),
    include_reflog: bool = False,
    candidate_file: Path | None = None,
    candidate_sha256: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Find path blobs reachable from local Git refs without mutating repositories."""

    if limit < 1:
        raise SystemExit("--limit must be at least 1")
    normalized_targets = list(dict.fromkeys(_git_target(target) for target in targets))
    if not normalized_targets:
        raise SystemExit("at least one --target is required")

    expected_sha = candidate_sha256.lower() if candidate_sha256 else None
    if expected_sha and (len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha)):
        raise SystemExit("--candidate-sha256 must be a 64-character hexadecimal SHA-256")
    resolved_candidate: Path | None = None
    file_sha: str | None = None
    if candidate_file is not None:
        resolved_candidate = candidate_file.expanduser().resolve()
        if not resolved_candidate.is_file():
            raise SystemExit(f"candidate file not found: {resolved_candidate}")
        file_sha = sha256_file(resolved_candidate)
        if expected_sha and expected_sha != file_sha:
            raise SystemExit("--candidate-file content does not match --candidate-sha256")
        expected_sha = file_sha

    repository_specs: list[tuple[Path, str]] = [(repo.expanduser().resolve(), "repo")]
    repository_specs.extend((mirror.expanduser().resolve(), "mirror") for mirror in mirrors)
    deduplicated: dict[str, tuple[Path, str]] = {}
    for path, role in repository_specs:
        key = os.path.normcase(str(path))
        if key not in deduplicated or role == "repo":
            deduplicated[key] = (path, role)

    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    repository_reports = []
    for repository, role in deduplicated.values():
        if _git_text(repository, "rev-parse", "--git-dir") is None:
            raise SystemExit(f"not a Git repository: {repository}")
        refs = _git_ref_tips(repository, include_reflog)
        repository_reports.append(
            {
                "path": str(repository),
                "role": role,
                "bare": _git_text(repository, "rev-parse", "--is-bare-repository") == "true",
                "refs_checked": len(refs),
            }
        )
        for target in normalized_targets:
            for version in _git_blob_versions(repository, target, refs):
                key = (version["target"], version["blob_sha256"])
                record = aggregate.setdefault(
                    key,
                    {
                        "target": version["target"],
                        "blob_sha256": version["blob_sha256"],
                        "bytes": version["bytes"],
                        "git_object_ids": set(),
                        "repositories": set(),
                        "refs": set(),
                        "commits": set(),
                    },
                )
                record["git_object_ids"].update(version["git_object_ids"])
                record["repositories"].add((str(repository), role))
                record["refs"].update(
                    (str(repository), name, kind) for name, kind in version["refs"]
                )
                record["commits"].update(
                    (str(repository), commit) for commit in version["commits"]
                )

    records = []
    for value in aggregate.values():
        records.append(
            {
                "target": value["target"],
                "blob_sha256": value["blob_sha256"],
                "bytes": value["bytes"],
                "candidate_match": expected_sha == value["blob_sha256"] if expected_sha else None,
                "git_object_ids": sorted(value["git_object_ids"]),
                "repositories": [
                    {"path": path, "role": role}
                    for path, role in sorted(value["repositories"])
                ],
                "refs": [
                    {"repository": path, "name": name, "kind": kind}
                    for path, name, kind in sorted(value["refs"])
                ],
                "commits": [
                    {"repository": path, "commit": commit}
                    for path, commit in sorted(value["commits"])
                ],
            }
        )
    records.sort(
        key=lambda record: (
            not bool(record["candidate_match"]),
            normalized_targets.index(str(record["target"])),
            str(record["blob_sha256"]),
        )
    )
    candidate_matches = [
        {
            "target": record["target"],
            "blob_sha256": record["blob_sha256"],
            "repositories": record["repositories"],
            "refs": record["refs"],
        }
        for record in records
        if record["candidate_match"]
    ]
    return {
        "repositories": repository_reports,
        "targets": normalized_targets,
        "include_reflog": include_reflog,
        "candidate": {
            "file": str(resolved_candidate) if resolved_candidate else None,
            "file_sha256": file_sha,
            "requested_sha256": candidate_sha256,
            "effective_sha256": expected_sha,
            "git_reachable": bool(candidate_matches) if expected_sha else None,
            "matches": candidate_matches,
        },
        "record_count": len(records),
        "records": records[:limit],
        "truncated": len(records) > limit,
    }


def query_git(args: argparse.Namespace) -> int:
    result = query_git_records(
        repo=args.repo,
        targets=args.target,
        mirrors=args.mirror,
        include_reflog=args.include_reflog,
        candidate_file=args.candidate_file,
        candidate_sha256=args.candidate_sha256,
        limit=args.limit,
    )
    result.update(
        {
            "schema_version": SCHEMA_VERSION,
            "mode": "dry-run" if args.dry_run else "query-only",
            "production_files_written": False,
            "worktree_modified": False,
            "index_modified": False,
            "stash_modified": False,
            "remote_contacted": False,
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def run_engine(args: argparse.Namespace) -> int:
    engine = args.engine.resolve()
    if not engine.is_file():
        raise SystemExit(f"recovery engine not found: {engine}")
    workspace = args.workspace_root.resolve()
    command = [sys.executable, str(engine), "--workspace-root", str(workspace), "--include-current"]
    for scope in args.scope:
        command.extend(["--scope", scope])
    if args.out:
        command.extend(["--out", str(args.out.resolve())])
    command.extend(["--from-date", args.from_date, "--to-date", args.to_date])
    if args.mode == "quick-index":
        command.append("--quick-index")
    elif args.mode == "plan":
        command.extend(["--latest-pass-only", "--skip-feature-catalog"])
    elif args.mode == "apply-missing":
        if args.confirm_workspace != str(workspace):
            raise SystemExit("apply refused: --confirm-workspace must exactly equal the resolved workspace root")
        command.extend(["--latest-pass-only", "--skip-feature-catalog", "--apply-latest-missing"])
    print(json.dumps({"engine_command": command, "historical_commands_executed": False}, ensure_ascii=False))
    return subprocess.run(command, check=False).returncode


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Archive and query AI session evidence without replaying historical commands.")
    sub = root.add_subparsers(dest="command", required=True)
    sources = sub.add_parser("sources", help="Discover configured Codex, Claude, Kimi, and OpenCode roots.")
    sources.set_defaults(func=lambda _args: (print(json.dumps(source_report(), ensure_ascii=False, indent=2)) or 0))

    archive = sub.add_parser("archive", help="Create or verify an append-only content-addressed archive.")
    archive_sub = archive.add_subparsers(dest="archive_command", required=True)
    create = archive_sub.add_parser("create")
    create.add_argument("--provider", action="append", choices=sorted(SOURCE_SPECS))
    create.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE)
    create.add_argument("--since", help="ISO timestamp; archive only files modified at or after it.")
    create.add_argument("--dry-run", action="store_true")
    create.set_defaults(func=archive_create)
    verify = archive_sub.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--sample", type=int, default=0)
    verify.set_defaults(func=archive_verify)

    opencode = sub.add_parser("opencode", help="Query OpenCode SQLite evidence read-only.")
    opencode_sub = opencode.add_subparsers(dest="opencode_command", required=True)
    query = opencode_sub.add_parser(
        "query", help="List newest Write/Edit/Patch/Bash/Read evidence without recovery writes."
    )
    query.add_argument(
        "--database",
        type=Path,
        default=expand("%USERPROFILE%/.local/share/opencode/opencode.db"),
    )
    query.add_argument("--tool", action="append", choices=OPENCODE_EVIDENCE_TOOLS)
    query.add_argument("--scope", action="append", default=[])
    query.add_argument("--since", help="ISO timestamp; only return evidence at or after it.")
    query.add_argument("--limit", type=int, default=100)
    query.add_argument(
        "--include-payloads",
        action="store_true",
        help="Include exact text payloads; default output contains hashes and short excerpts.",
    )
    query.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly label the already read-only query as a dry run.",
    )
    query.set_defaults(func=opencode_query)

    generic_query = sub.add_parser(
        "query", help="Query previously indexed recovery evidence without reading payload bodies."
    )
    generic_query_sub = generic_query.add_subparsers(dest="query_command", required=True)
    path_query = generic_query_sub.add_parser(
        "path", help="Find target paths in latest-complete ledgers and re-hash current files."
    )
    path_query.add_argument("--target", action="append", required=True)
    path_query.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER_ROOT)
    path_query.add_argument("--ledger", type=Path, action="append", default=[])
    path_query.add_argument("--limit", type=int, default=100)
    path_query.add_argument("--dry-run", action="store_true")
    path_query.set_defaults(func=query_path)

    git_query = generic_query_sub.add_parser(
        "git", help="Find target file blobs reachable from local Git refs and mirrors."
    )
    git_query.add_argument("--repo", type=Path, required=True)
    git_query.add_argument("--target", action="append", required=True)
    git_query.add_argument("--candidate-file", type=Path)
    git_query.add_argument("--candidate-sha256")
    git_query.add_argument("--mirror", type=Path, action="append", default=[])
    git_query.add_argument("--include-reflog", action="store_true")
    git_query.add_argument("--limit", type=int, default=100)
    git_query.add_argument("--dry-run", action="store_true")
    git_query.set_defaults(func=query_git)

    engine = sub.add_parser("engine", help="Invoke the proven latest-complete-file engine through safety presets.")
    engine.add_argument("mode", choices=["quick-index", "plan", "apply-missing"])
    engine.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    engine.add_argument("--workspace-root", type=Path, default=Path("C:/workspace/"))
    engine.add_argument("--scope", action="append", default=[])
    engine.add_argument("--out", type=Path)
    engine.add_argument("--from-date", default="2026-07-18")
    engine.add_argument("--to-date", default=dt.date.today().isoformat())
    engine.add_argument("--confirm-workspace", default="")
    engine.set_defaults(func=run_engine)
    add_portable_parsers(sub)
    add_baseline_parser(sub)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
