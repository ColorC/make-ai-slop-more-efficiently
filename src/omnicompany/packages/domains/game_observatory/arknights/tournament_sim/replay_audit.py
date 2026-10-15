# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T00:00:00+08:00 type=replay-audit status=active
"""Strict serialized replay verifier for offline and third-party audit tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .engine import verify_replay
from .models import BattleReplay, ContentBundle, canonical_sha256

MAX_REPLAY_BYTES = 64 * 1024 * 1024


class SerializedReplayError(ValueError):
    """Raised when serialized replay input cannot enter the audit boundary."""


@dataclass(frozen=True)
class SerializedReplayAudit:
    valid: bool
    issues: tuple[str, ...]
    replay_hash: str | None = None
    final_snapshot_hash: str | None = None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SerializedReplayError("replay JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise SerializedReplayError(f"replay JSON contains a non-finite number: {value}")


def parse_serialized_replay(payload: bytes) -> BattleReplay:
    """Parse one bounded, strict UTF-8 JSON replay without executing commands."""

    if not isinstance(payload, bytes):
        raise SerializedReplayError("serialized replay must be bytes")
    if not payload or len(payload) > MAX_REPLAY_BYTES:
        raise SerializedReplayError("serialized replay size is outside the allowed range")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SerializedReplayError("serialized replay must be UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, RecursionError, SerializedReplayError) as exc:
        raise SerializedReplayError("serialized replay is not strict JSON") from exc
    if not isinstance(value, dict):
        raise SerializedReplayError("serialized replay root must be an object")
    try:
        return BattleReplay.model_validate(value)
    except ValidationError as exc:
        raise SerializedReplayError("serialized replay violates its frozen schema") from exc


def _audit_chains(replay: BattleReplay) -> tuple[str, ...]:
    """Validate cryptographic/order invariants without invoking the battle kernel."""

    issues: list[str] = []
    body = replay.model_dump(mode="json", exclude={"replay_hash"})
    if replay.replay_hash != canonical_sha256(body):
        issues.append("replay hash mismatch")

    previous_record_hash: str | None = None
    previous_snapshot_hash: str | None = None
    expected_sequence = replay.initial_state.command_sequence
    expected_event_count = 0
    for record in replay.commands:
        expected_sequence += 1
        record_body = record.model_dump(mode="json", exclude={"record_hash"})
        if record.sequence != expected_sequence:
            issues.append("command sequence is not contiguous")
        if record.previous_record_hash != previous_record_hash:
            issues.append("command hash predecessor mismatch")
        if record.record_hash != canonical_sha256(record_body):
            issues.append("command record hash mismatch")
        if previous_snapshot_hash is not None and (
            record.snapshot_hash_before != previous_snapshot_hash
        ):
            issues.append("command snapshot chain mismatch")
        if record.event_count_before != expected_event_count:
            issues.append("command event-count chain mismatch")
        if record.event_count_after < record.event_count_before:
            issues.append("command event count moves backwards")
        previous_record_hash = record.record_hash
        previous_snapshot_hash = record.snapshot_hash_after
        expected_event_count = record.event_count_after
    if replay.commands:
        if previous_snapshot_hash != replay.final_snapshot_hash:
            issues.append("final snapshot is not the command-chain head")
        if expected_event_count != len(replay.events):
            issues.append("final event count differs from the event stream")
    elif replay.events:
        issues.append("event stream exists without a command stream")

    ordering_keys = [event.ordering_key for event in replay.events]
    if ordering_keys != sorted(ordering_keys):
        issues.append("event stream ordering is invalid")
    event_sequences = sorted(event.sequence for event in replay.events)
    expected_event_sequences = list(
        range(
            replay.initial_state.event_sequence + 1,
            replay.initial_state.event_sequence + 1 + len(replay.events),
        )
    )
    if event_sequences != expected_event_sequences:
        issues.append("event sequence is not contiguous")

    previous_digest_hash = replay.initial_state.last_tick_digest_hash
    expected_digest_index = replay.initial_state.tick_digest_count
    expected_tick = replay.initial_state.tick
    for digest in replay.tick_digests:
        expected_digest_index += 1
        expected_tick += 1
        if digest.digest_index != expected_digest_index or digest.tick != expected_tick:
            issues.append("tick digest sequence is not contiguous")
        if digest.previous_digest_hash != previous_digest_hash or not digest.verify_hash():
            issues.append("tick digest hash chain mismatch")
        previous_digest_hash = digest.digest_hash
    return tuple(dict.fromkeys(issues))


def audit_serialized_replay(
    content: ContentBundle,
    payload: bytes,
) -> SerializedReplayAudit:
    """Strictly parse, independently audit chains, then re-execute the replay."""

    try:
        replay = parse_serialized_replay(payload)
    except SerializedReplayError as exc:
        return SerializedReplayAudit(valid=False, issues=(str(exc),))
    issues = list(_audit_chains(replay))
    semantic = verify_replay(content, replay)
    issues.extend(semantic.issues)
    unique_issues = tuple(dict.fromkeys(issues))
    return SerializedReplayAudit(
        valid=not unique_issues,
        issues=unique_issues,
        replay_hash=replay.replay_hash,
        final_snapshot_hash=semantic.final_snapshot_hash,
    )


__all__ = [
    "MAX_REPLAY_BYTES",
    "SerializedReplayAudit",
    "SerializedReplayError",
    "audit_serialized_replay",
    "parse_serialized_replay",
]
