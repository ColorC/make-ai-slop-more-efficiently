# [OMNI] material_id="material:core.hr.claim.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="跨会话写入意向账本：路径声明、TTL、冲突检测、释放与标准协商消息"
# [OMNI] tags=hr,claim,write-intent,coordination,pty
"""Append-only, cross-process-safe write-intent claims."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterable, Iterator
from uuid import uuid4


CLAIMS_FILENAME = "claims.jsonl"
CLAIM_SCHEMA = "omnicompany.hr-write-claim.v1"
DEFAULT_TTL_SECONDS = 4 * 60 * 60
MAX_TTL_SECONDS = 24 * 60 * 60

_THREAD_LOCK = threading.RLock()


class ClaimRejected(ValueError):
    """A claim operation is malformed or not owned by the caller."""


class ClaimConflictError(RuntimeError):
    """A new claim overlaps one or more active claims owned by another session."""

    def __init__(self, conflicts: list[dict[str, Any]]):
        super().__init__(f"write claim conflicts with {len(conflicts)} active claim(s)")
        self.conflicts = conflicts

    def to_dict(self) -> dict[str, Any]:
        return {"error": "claim_conflict", "conflicts": self.conflicts}


@dataclass(frozen=True)
class ClaimTarget:
    path: str
    recursive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WriteClaim:
    id: str
    created_at: str
    updated_at: str
    expires_at: str
    status: str
    purpose: str
    targets: list[dict[str, Any]]
    owner_trace_id: str
    owner_session_id: str = ""
    owner_pty_id: str = ""
    owner_provider: str = ""
    project: str = ""
    workstream: str = ""
    plan: str = ""
    task: str = ""
    topic: str = ""
    release_reason: str = ""
    schema: str = CLAIM_SCHEMA
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def claims_path(data_dir: Path) -> Path:
    return Path(data_dir) / CLAIMS_FILENAME


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _normalize_target(raw: str, *, recursive: bool, cwd: Path) -> ClaimTarget:
    text = str(raw or "").strip()
    if not text:
        raise ClaimRejected("claim path cannot be empty")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    try:
        normalized = candidate.resolve(strict=False)
    except OSError:
        normalized = candidate.absolute()
    return ClaimTarget(path=str(normalized), recursive=recursive)


def _contains(tree: str, candidate: str) -> bool:
    tree_key = _path_key(tree)
    candidate_key = _path_key(candidate)
    try:
        return os.path.commonpath([tree_key, candidate_key]) == tree_key
    except ValueError:
        return False


def targets_overlap(left: ClaimTarget, right: ClaimTarget) -> bool:
    if _path_key(left.path) == _path_key(right.path):
        return True
    if left.recursive and _contains(left.path, right.path):
        return True
    if right.recursive and _contains(right.path, left.path):
        return True
    return False


def _targets_from_claim(claim: WriteClaim) -> list[ClaimTarget]:
    return [
        ClaimTarget(path=str(row.get("path") or ""), recursive=bool(row.get("recursive")))
        for row in claim.targets
        if str(row.get("path") or "")
    ]


def _fingerprint(owner_trace_id: str, purpose: str, targets: Iterable[ClaimTarget]) -> str:
    payload = {
        "owner_trace_id": owner_trace_id,
        "purpose": purpose.strip(),
        "targets": sorted(
            ({"path": _path_key(target.path), "recursive": target.recursive} for target in targets),
            key=lambda row: (row["path"], row["recursive"]),
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "claim_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@contextmanager
def _claim_guard(data_dir: Path) -> Iterator[None]:
    lock_path = claims_path(data_dir).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_current_unlocked(data_dir: Path) -> dict[str, WriteClaim]:
    path = claims_path(data_dir)
    if not path.is_file():
        return {}
    known = set(WriteClaim.__dataclass_fields__)
    current: dict[str, WriteClaim] = {}
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("schema") != CLAIM_SCHEMA:
                continue
            try:
                claim = WriteClaim(**{key: value for key, value in row.items() if key in known})
            except TypeError:
                continue
            current[claim.id] = claim
    return current


def _active(claim: WriteClaim, now: datetime) -> bool:
    expires = _parse_time(claim.expires_at)
    return claim.status == "active" and expires is not None and expires > now


def read_claims(
    *, data_dir: Path, include_inactive: bool = False, now: datetime | None = None
) -> list[WriteClaim]:
    current = _read_current_unlocked(data_dir)
    check_at = now or _now()
    rows = list(current.values())
    if not include_inactive:
        rows = [row for row in rows if _active(row, check_at)]
    return sorted(rows, key=lambda row: (row.created_at, row.id))


def _append_unlocked(data_dir: Path, claim: WriteClaim) -> None:
    path = claims_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(claim.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(descriptor, raw)
    finally:
        os.close(descriptor)


def _owner_value(owner: dict[str, Any], field: str, fallback: str = "") -> str:
    return str(owner.get(field) or fallback).strip()


def _conflicts(
    requested: list[ClaimTarget], active_claims: Iterable[WriteClaim], owner_trace_id: str
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for claim in active_claims:
        if claim.owner_trace_id == owner_trace_id:
            continue
        matches = [
            {"requested": left.to_dict(), "held": right.to_dict()}
            for left in requested
            for right in _targets_from_claim(claim)
            if targets_overlap(left, right)
        ]
        if matches:
            conflicts.append({
                "claim_id": claim.id,
                "owner_trace_id": claim.owner_trace_id,
                "owner_session_id": claim.owner_session_id,
                "owner_pty_id": claim.owner_pty_id,
                "owner_provider": claim.owner_provider,
                "purpose": claim.purpose,
                "expires_at": claim.expires_at,
                "matches": matches,
                "negotiation_target": claim.owner_pty_id or claim.owner_session_id or claim.owner_trace_id,
            })
    return conflicts


def create_claim(
    *,
    data_dir: Path,
    owner: dict[str, Any],
    exact_paths: Iterable[str] = (),
    tree_paths: Iterable[str] = (),
    purpose: str,
    cwd: Path,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> tuple[WriteClaim, bool]:
    """Create one claim; returns ``(claim, idempotent_reuse)``."""
    owner_trace_id = _owner_value(owner, "trace_id")
    if not owner_trace_id or owner_trace_id.startswith("cc_unknown_"):
        raise ClaimRejected("current session has no stable trace_id")
    purpose = str(purpose or "").strip()
    if not purpose:
        raise ClaimRejected("purpose cannot be empty")
    if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
        raise ClaimRejected(f"ttl_seconds must be within 1..{MAX_TTL_SECONDS}")
    targets = [
        *(_normalize_target(path, recursive=False, cwd=Path(cwd)) for path in exact_paths),
        *(_normalize_target(path, recursive=True, cwd=Path(cwd)) for path in tree_paths),
    ]
    if not targets:
        raise ClaimRejected("at least one --path or --tree is required")
    deduped: dict[tuple[str, bool], ClaimTarget] = {
        (_path_key(target.path), target.recursive): target for target in targets
    }
    targets = list(deduped.values())
    fingerprint = _fingerprint(owner_trace_id, purpose, targets)
    created_at = now or _now()

    with _claim_guard(data_dir):
        current = list(_read_current_unlocked(data_dir).values())
        active_claims = [row for row in current if _active(row, created_at)]
        conflicts = _conflicts(targets, active_claims, owner_trace_id)
        if conflicts:
            raise ClaimConflictError(conflicts)
        for prior in active_claims:
            if prior.fingerprint == fingerprint:
                return prior, True
        claim = WriteClaim(
            id=f"claim_{uuid4().hex}",
            created_at=_iso(created_at),
            updated_at=_iso(created_at),
            expires_at=_iso(created_at + timedelta(seconds=ttl_seconds)),
            status="active",
            purpose=purpose,
            targets=[target.to_dict() for target in targets],
            owner_trace_id=owner_trace_id,
            owner_session_id=_owner_value(owner, "session_id", _owner_value(owner, "claude_session_id")),
            owner_pty_id=_owner_value(owner, "pty_id"),
            owner_provider=_owner_value(owner, "provider"),
            project=_owner_value(owner, "project"),
            workstream=_owner_value(owner, "workstream"),
            plan=_owner_value(owner, "active_plan", _owner_value(owner, "plan")),
            task=_owner_value(owner, "task_id", _owner_value(owner, "task")),
            topic=_owner_value(owner, "topic"),
            fingerprint=fingerprint,
        )
        _append_unlocked(data_dir, claim)
        return claim, False


def release_claim(
    claim_id: str,
    *,
    data_dir: Path,
    owner_trace_id: str,
    reason: str,
    now: datetime | None = None,
) -> WriteClaim:
    when = now or _now()
    with _claim_guard(data_dir):
        claim = _read_current_unlocked(data_dir).get(claim_id)
        if claim is None:
            raise ClaimRejected(f"unknown claim_id: {claim_id}")
        if claim.owner_trace_id != owner_trace_id:
            raise ClaimRejected("only the owning session can release a claim")
        if claim.status != "active":
            return claim
        released = WriteClaim(**{
            **claim.to_dict(),
            "updated_at": _iso(when),
            "status": "released",
            "release_reason": str(reason or "completed").strip() or "completed",
        })
        _append_unlocked(data_dir, released)
        return released


def renew_claim(
    claim_id: str,
    *,
    data_dir: Path,
    owner_trace_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> WriteClaim:
    if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
        raise ClaimRejected(f"ttl_seconds must be within 1..{MAX_TTL_SECONDS}")
    when = now or _now()
    with _claim_guard(data_dir):
        claim = _read_current_unlocked(data_dir).get(claim_id)
        if claim is None:
            raise ClaimRejected(f"unknown claim_id: {claim_id}")
        if claim.owner_trace_id != owner_trace_id:
            raise ClaimRejected("only the owning session can renew a claim")
        if claim.status != "active":
            raise ClaimRejected("released claims cannot be renewed")
        renewed = WriteClaim(**{
            **claim.to_dict(),
            "updated_at": _iso(when),
            "expires_at": _iso(when + timedelta(seconds=ttl_seconds)),
        })
        _append_unlocked(data_dir, renewed)
        return renewed


def find_write_conflicts(
    path: str,
    *,
    data_dir: Path,
    owner_trace_id: str,
    cwd: Path,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fast hook-facing lookup. Missing stores and expired claims are harmless."""
    if not claims_path(data_dir).is_file():
        return []
    requested = [_normalize_target(path, recursive=False, cwd=Path(cwd))]
    return _conflicts(requested, read_claims(data_dir=data_dir, now=now), owner_trace_id)


def build_negotiation_request(
    *, requester: dict[str, Any], conflict: dict[str, Any], proposal: str
) -> str:
    proposal = " ".join(str(proposal or "").split())
    if not proposal:
        raise ClaimRejected("proposal cannot be empty")
    return (
        "HR_CLAIM_NEGOTIATION v1 | "
        f"conflicting_claim={conflict.get('claim_id')} | "
        f"requester={requester.get('trace_id')} | "
        f"requester_topic={requester.get('topic') or '-'} | "
        f"overlap={json.dumps(conflict.get('matches') or [], ensure_ascii=False)} | "
        f"proposal={proposal} | "
        "options=release_claim|split_scope|keep_until_expiry | "
        "on_agreement=owner runs `omni hr release <claim_id> --reason negotiated`, "
        "then requester retries `omni hr claim`."
    )


__all__ = [
    "CLAIMS_FILENAME", "CLAIM_SCHEMA", "DEFAULT_TTL_SECONDS", "MAX_TTL_SECONDS",
    "ClaimRejected", "ClaimConflictError", "ClaimTarget", "WriteClaim",
    "claims_path", "targets_overlap", "read_claims", "create_claim", "release_claim",
    "renew_claim", "find_write_conflicts", "build_negotiation_request",
]
