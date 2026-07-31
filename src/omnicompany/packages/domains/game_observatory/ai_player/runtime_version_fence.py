"""Frozen-runtime contract probes for detached AI-player drives.

The detached worker and every provider child inherit a content-addressed source
snapshot.  This module compares that frozen runtime with the live checkout at
turn boundaries without opening the Observatory database or touching a device.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..subprocess_policy import headless_process_kwargs


RUNTIME_CONTRACT_SCHEMA = "game-observatory.ai-player.runtime-contract.v1"
RUNTIME_FENCE_SIGNAL_SCHEMA = "game-observatory.ai-player.runtime-fence-signal.v1"
EXPECTED_CONTRACT_ENV = "OMNI_GAME_PLAYER_EXPECTED_RUNTIME_CONTRACT"
LIVE_REPOSITORY_ENV = "OMNI_GAME_PLAYER_LIVE_REPOSITORY_ROOT"
FENCE_SIGNAL_ENV = "OMNI_GAME_PLAYER_RUNTIME_FENCE_SIGNAL"
OBSERVATORY_ROOT_ENV = "OMNI_GAME_PLAYER_OBSERVATORY_ROOT"
SNAPSHOT_REPOSITORY_ENV = "OMNI_GAME_PLAYER_RUNTIME_SNAPSHOT_ROOT"


class RuntimeContractProbeError(RuntimeError):
    """Raised when a runtime contract cannot be obtained without side effects."""


@dataclass(frozen=True)
class PlayerRuntimeContract:
    schema: str
    ai_player_schema_version: int
    ai_player_schema_manifest_sha256: str
    facility_contract_sha256: str
    source_probe_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PlayerRuntimeContract":
        if payload.get("schema") != RUNTIME_CONTRACT_SCHEMA:
            raise RuntimeContractProbeError("unsupported AI-player runtime contract schema")
        return cls(
            schema=RUNTIME_CONTRACT_SCHEMA,
            ai_player_schema_version=int(payload["ai_player_schema_version"]),
            ai_player_schema_manifest_sha256=str(
                payload["ai_player_schema_manifest_sha256"]
            ),
            facility_contract_sha256=str(payload["facility_contract_sha256"]),
            source_probe_sha256=str(payload["source_probe_sha256"]),
        )

    def semantically_matches(self, other: "PlayerRuntimeContract") -> bool:
        return (
            self.ai_player_schema_version == other.ai_player_schema_version
            and self.ai_player_schema_manifest_sha256
            == other.ai_player_schema_manifest_sha256
            and self.facility_contract_sha256 == other.facility_contract_sha256
            and self.source_probe_sha256 == other.source_probe_sha256
        )


@dataclass(frozen=True)
class PlayerDatabaseSchemaState:
    ai_player_schema_version: int
    sqlite_user_version: int


def _sha256_parts(parts: list[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _source_root(repository_root: Path) -> Path:
    source_root = repository_root.resolve() / "src" / "omnicompany"
    if not source_root.is_dir():
        raise RuntimeContractProbeError(
            f"AI-player runtime source root does not exist: {source_root}"
        )
    return source_root


def _schema_contract(repository_root: Path) -> tuple[int, str, list[bytes]]:
    ai_player_root = (
        _source_root(repository_root)
        / "packages"
        / "domains"
        / "game_observatory"
        / "ai_player"
    )
    store_path = ai_player_root / "store.py"
    try:
        store_bytes = store_path.read_bytes()
        tree = ast.parse(store_bytes, filename=str(store_path))
    except (OSError, SyntaxError) as exc:
        raise RuntimeContractProbeError(f"cannot parse AI-player store schema: {exc}") from exc
    schema_version: int | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "AI_PLAYER_SCHEMA_VERSION"
            for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
            schema_version = node.value.value
            break
    if schema_version is None or schema_version < 1:
        raise RuntimeContractProbeError("AI_PLAYER_SCHEMA_VERSION must be a positive literal")
    migration_root = ai_player_root / "migrations"
    migrations = sorted(migration_root.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not migrations:
        raise RuntimeContractProbeError("AI-player migration files are missing")
    migration_parts: list[bytes] = []
    for path in migrations:
        try:
            migration_parts.extend(
                [path.name.encode("utf-8"), hashlib.sha256(path.read_bytes()).digest()]
            )
        except OSError as exc:
            raise RuntimeContractProbeError(f"cannot read migration {path}: {exc}") from exc
    manifest = _sha256_parts(
        [str(schema_version).encode("ascii"), *migration_parts]
    )
    return schema_version, manifest, migration_parts


def runtime_contract_source_probe_sha256(repository_root: Path) -> str:
    """Hash all Python inputs that can change detached AI-player behavior."""

    source_root = _source_root(repository_root)
    game_observatory_root = (
        source_root
        / "packages"
        / "domains"
        / "game_observatory"
    )
    schema_version, schema_manifest, _migration_parts = _schema_contract(repository_root)
    runtime_paths = sorted(game_observatory_root.rglob("*.py"))
    cli_path = source_root / "cli" / "commands" / "game.py"
    if cli_path.is_file():
        runtime_paths.append(cli_path)
    runtime_parts: list[bytes] = []
    for path in sorted(set(runtime_paths)):
        try:
            relative = path.relative_to(source_root).as_posix().encode("utf-8")
            runtime_parts.extend(
                [relative, hashlib.sha256(path.read_bytes()).digest()]
            )
        except OSError as exc:
            raise RuntimeContractProbeError(
                f"cannot read AI-player runtime source {path}: {exc}"
            ) from exc
    return _sha256_parts(
        [
            str(schema_version).encode("ascii"),
            schema_manifest.encode("ascii"),
            *runtime_parts,
        ]
    )


def _facility_contract_from_live_source(
    repository_root: Path,
    *,
    timeout_seconds: float,
) -> str:
    script = """
import json
import sys
sys.path.insert(0, sys.argv[1])
from omnicompany.packages.domains.game_observatory.ai_player.external_agent_continuity import build_player_facility_contract
print(json.dumps({"facility_contract_sha256": build_player_facility_contract().facility_contract_sha256}))
""".strip()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(repository_root / "src")],
            cwd=str(repository_root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            **headless_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeContractProbeError(f"facility contract probe failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-2000:]
        raise RuntimeContractProbeError(
            f"facility contract probe exited {completed.returncode}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        contract_hash = str(payload["facility_contract_sha256"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeContractProbeError("facility contract probe returned invalid JSON") from exc
    if len(contract_hash) != 64:
        raise RuntimeContractProbeError("facility contract probe returned an invalid hash")
    return contract_hash


@lru_cache(maxsize=16)
def _probe_cached(
    repository_root_text: str,
    source_probe_sha256: str,
    timeout_seconds: float,
) -> PlayerRuntimeContract:
    repository_root = Path(repository_root_text)
    schema_version, schema_manifest, _migration_parts = _schema_contract(repository_root)
    facility_hash = _facility_contract_from_live_source(
        repository_root,
        timeout_seconds=timeout_seconds,
    )
    return PlayerRuntimeContract(
        schema=RUNTIME_CONTRACT_SCHEMA,
        ai_player_schema_version=schema_version,
        ai_player_schema_manifest_sha256=schema_manifest,
        facility_contract_sha256=facility_hash,
        source_probe_sha256=source_probe_sha256,
    )


def probe_runtime_contract(
    repository_root: Path,
    *,
    timeout_seconds: float = 15.0,
) -> PlayerRuntimeContract:
    """Read contract/schema truth from one source tree without DB or device access."""

    root = repository_root.resolve()
    source_digest = runtime_contract_source_probe_sha256(root)
    return _probe_cached(str(root), source_digest, float(timeout_seconds))


def write_runtime_contract(path: Path, contract: PlayerRuntimeContract) -> None:
    payload = (
        json.dumps(
            contract.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_runtime_contract(path: Path) -> PlayerRuntimeContract:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeContractProbeError(f"cannot read runtime contract {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeContractProbeError("runtime contract must be a JSON object")
    return PlayerRuntimeContract.from_dict(payload)


def read_player_database_schema(observatory_root: Path) -> PlayerDatabaseSchemaState:
    """Read schema metadata through SQLite read-only mode without creating a DB."""

    db_path = observatory_root.resolve() / "observatory.sqlite3"
    if not db_path.is_file():
        raise RuntimeContractProbeError(f"Observatory database does not exist: {db_path}")
    uri = db_path.as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            connection.execute("PRAGMA query_only=ON")
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            row = connection.execute(
                "SELECT version FROM ai_player_schema_version WHERE id=1"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RuntimeContractProbeError(
            f"cannot read AI-player database schema in read-only mode: {exc}"
        ) from exc
    if row is None:
        raise RuntimeContractProbeError("AI-player database schema row is missing")
    return PlayerDatabaseSchemaState(
        ai_player_schema_version=int(row[0]),
        sqlite_user_version=user_version,
    )


def _write_fence_signal(path: Path, detail: dict[str, Any]) -> None:
    payload = {
        "schema": RUNTIME_FENCE_SIGNAL_SCHEMA,
        "stop_reason": "facility_contract_change",
        "recoverable": True,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        **detail,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def detect_runtime_facility_contract_change() -> dict[str, Any] | None:
    """Return a recoverable fence signal when a detached run outlives its facility.

    Direct foreground CLI calls do not set the three environment variables and
    therefore retain their existing behavior.
    """

    expected_path_text = os.environ.get(EXPECTED_CONTRACT_ENV)
    live_root_text = os.environ.get(LIVE_REPOSITORY_ENV)
    signal_path_text = os.environ.get(FENCE_SIGNAL_ENV)
    observatory_root_text = os.environ.get(OBSERVATORY_ROOT_ENV)
    snapshot_root_text = os.environ.get(SNAPSHOT_REPOSITORY_ENV)
    if (
        not expected_path_text
        and not live_root_text
        and not signal_path_text
        and not observatory_root_text
        and not snapshot_root_text
    ):
        return None
    if (
        not expected_path_text
        or not live_root_text
        or not signal_path_text
        or not observatory_root_text
        or not snapshot_root_text
    ):
        detail = {
            "change_kind": "runtime_fence_environment_incomplete",
            "error": "detached runtime fence environment is incomplete",
        }
        if signal_path_text:
            _write_fence_signal(Path(signal_path_text), detail)
        return detail
    expected_path = Path(expected_path_text)
    live_root = Path(live_root_text)
    signal_path = Path(signal_path_text)
    try:
        # Imported lazily to keep runtime_snapshot -> runtime_version_fence acyclic.
        from .runtime_snapshot import RuntimeSnapshotError, verify_runtime_snapshot

        verify_runtime_snapshot(Path(snapshot_root_text))
    except RuntimeSnapshotError as exc:
        detail = {
            "change_kind": "frozen_runtime_integrity_change",
            "error": str(exc),
        }
        _write_fence_signal(signal_path, detail)
        return detail
    try:
        expected = read_runtime_contract(expected_path)
        current = probe_runtime_contract(live_root)
        database = read_player_database_schema(Path(observatory_root_text))
    except RuntimeContractProbeError as exc:
        detail = {
            "change_kind": "live_runtime_contract_probe_failed",
            "error": str(exc),
        }
        _write_fence_signal(signal_path, detail)
        return detail
    if database.ai_player_schema_version > expected.ai_player_schema_version:
        detail = {
            "change_kind": "database_schema_newer_than_frozen_runtime",
            "expected": expected.to_dict(),
            "database": asdict(database),
        }
        _write_fence_signal(signal_path, detail)
        return detail
    if expected.semantically_matches(current):
        return None
    detail = {
        "change_kind": "facility_contract_or_schema_changed",
        "expected": expected.to_dict(),
        "current": current.to_dict(),
    }
    _write_fence_signal(signal_path, detail)
    return detail


__all__ = [
    "EXPECTED_CONTRACT_ENV",
    "FENCE_SIGNAL_ENV",
    "LIVE_REPOSITORY_ENV",
    "OBSERVATORY_ROOT_ENV",
    "SNAPSHOT_REPOSITORY_ENV",
    "PlayerDatabaseSchemaState",
    "PlayerRuntimeContract",
    "RUNTIME_CONTRACT_SCHEMA",
    "RUNTIME_FENCE_SIGNAL_SCHEMA",
    "RuntimeContractProbeError",
    "detect_runtime_facility_contract_change",
    "probe_runtime_contract",
    "read_runtime_contract",
    "read_player_database_schema",
    "runtime_contract_source_probe_sha256",
    "write_runtime_contract",
]
