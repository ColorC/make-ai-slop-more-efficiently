# [OMNI] origin=claude created_by=claude intent="serve validated battle memory frames to contestants without ever inventing one"
"""One battle frame, stamped with where it came from and whether to trust it.

This is the consumer-facing edge of the read-only memory channel.  Everything
it returns is either a frame that passed every structural check or an explicit
refusal telling the caller to fall back to OCR.  There is no middle value: a
reclaimed object, a restarted process or a battle that has not loaded all
produce ``available: false``, never a plausible-looking number.

Addresses are cached against the process identity so a contestant pays the
discovery cost once rather than once per query.  The class pointer survives
individual battles and is reused even when the controller has to be found
again; the whole cache is discarded the moment the process behind it changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_locator import (
    AdbProcessMemoryReader,
    LocatedBattleAddresses,
    MemoryLocatorError,
    ProcessIdentity,
    TORAPPU_2_7_61,
    load_located_addresses,
    locate_battle_addresses,
)
from .memory_observer import (
    BattleMemorySnapshot,
    ContinuousAdbMemoryProbe,
    MemoryObservationError,
)

__all__ = [
    "BattleChannelError",
    "BattleObservationEnvelope",
    "read_battle_state",
    "resolve_addresses",
]


class BattleChannelError(RuntimeError):
    """The channel cannot serve a trustworthy frame right now."""


@dataclass(frozen=True, slots=True)
class BattleObservationEnvelope:
    """A frame plus the provenance a decision layer needs to judge it."""

    available: bool
    captured_at: str
    source: str
    validity: str
    version_profile: str
    value: dict[str, Any] | None = None
    reason: str = ""
    fallback: str = ""

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_id": "game-observatory.arknights-battle-observation.v1",
            "available": self.available,
            "captured_at": self.captured_at,
            "source": self.source,
            "validity": self.validity,
            "version_profile": self.version_profile,
        }
        if self.value is not None:
            payload["value"] = self.value
        if self.reason:
            payload["reason"] = self.reason
        if self.fallback:
            payload["fallback"] = self.fallback
        return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cached_class(cache_path: Path, identity: ProcessIdentity) -> int | None:
    """Reuse the class pointer only while the very same process is alive."""

    if not cache_path.is_file():
        return None
    try:
        cached = load_located_addresses(cache_path)
    except (OSError, ValueError, KeyError):
        return None
    if not cached.identity.matches(identity):
        return None
    return cached.addresses.controller_class


def resolve_addresses(
    reader: AdbProcessMemoryReader,
    cache_path: Path,
    *,
    native_locator_path: str | None = None,
) -> LocatedBattleAddresses:
    """Return usable addresses, relocating whenever the cached ones are stale.

    A cached controller is accepted only if its class pointer still reads back,
    which is what a garbage-collected battle object fails.
    """

    identity = reader.identity()
    if cache_path.is_file():
        try:
            cached = load_located_addresses(cache_path)
        except (OSError, ValueError, KeyError):
            cached = None
        if cached is not None and cached.identity.matches(identity):
            head = reader.read(cached.addresses.controller, 8)
            if len(head) == 8 and int.from_bytes(head, "little") == (
                cached.addresses.controller_class
            ):
                return cached

    hint = None
    if cache_path.is_file():
        try:
            hint = load_located_addresses(cache_path)
        except (OSError, ValueError, KeyError):
            hint = None
        if hint is not None and not hint.identity.matches(identity):
            hint = None
    located = locate_battle_addresses(
        reader,
        controller_class=_cached_class(cache_path, identity),
        prefer_region=hint.controller_region if hint else None,
        native_locator_path=native_locator_path,
    )
    located.write(cache_path)
    return located


def _frame_to_value(snapshot: BattleMemorySnapshot) -> dict[str, Any]:
    remaining = max(0, snapshot.life_points - snapshot.valid_missed_enemies)
    return {
        # A fresh controller is allocated for every battle.  Exposing this
        # read-only identity lets resident action caches reset exactly at the
        # battle boundary instead of leaking placements into the next stage.
        "controller": snapshot.controller,
        "loaded": snapshot.loaded,
        "battle_state": snapshot.battle_state,
        "battle_result": snapshot.battle_result,
        "speed_level": snapshot.speed_level,
        "real_play_time": snapshot.real_play_time,
        "paused": snapshot.paused,
        # life_points is the stage's life ceiling, not the live counter; the
        # remaining value has to be derived or a caller reads full life at the
        # exact moment it has none.
        "life_ceiling": snapshot.life_points,
        "remaining_life": remaining,
        "current_cost": snapshot.current_cost,
        "minimum_cost": snapshot.minimum_cost,
        "maximum_cost": snapshot.maximum_cost,
        "cost_progress": snapshot.cost_progress,
        "cost_period_seconds": snapshot.cost_period_seconds,
        "cost_remaining_seconds": snapshot.cost_remaining_seconds,
        "cost_locked": snapshot.cost_locked,
        "negative_recovery_multiplier": snapshot.negative_recovery_multiplier,
        "spawned_enemies": snapshot.spawned_enemies,
        "spawned_waves": snapshot.spawned_waves,
        "killed_enemies": snapshot.killed_enemies,
        "valid_killed_enemies": snapshot.valid_killed_enemies,
        "leaked_enemies": snapshot.valid_missed_enemies,
        "total_enemies": snapshot.total_enemies,
    }


def read_battle_state(
    *,
    adb_path: Path | str,
    serial: str,
    package: str,
    helper_path: str,
    cache_path: Path,
) -> BattleObservationEnvelope:
    """Sample one battle frame, or explain why there is nothing to trust."""

    profile = TORAPPU_2_7_61.profile_id
    try:
        reader = AdbProcessMemoryReader(
            adb_path=adb_path, serial=serial, package=package
        )
        located = resolve_addresses(reader, cache_path)
    except MemoryLocatorError as error:
        return BattleObservationEnvelope(
            available=False,
            captured_at=_now(),
            source="memory",
            validity="unavailable",
            version_profile=profile,
            reason=str(error),
            fallback="ocr",
        )

    try:
        with ContinuousAdbMemoryProbe(
            adb_path=adb_path,
            serial=serial,
            helper_path=helper_path,
            addresses=located.addresses,
            sample_hz=10,
        ) as probe:
            snapshot = probe.sample()
    except MemoryObservationError as error:
        # A structural check failed, so the addresses are suspect. Drop the
        # cache rather than serving the same bad frame on the next call.
        cache_path.unlink(missing_ok=True)
        return BattleObservationEnvelope(
            available=False,
            captured_at=_now(),
            source="memory",
            validity="rejected",
            version_profile=profile,
            reason=str(error),
            fallback="ocr",
        )

    return BattleObservationEnvelope(
        available=True,
        captured_at=_now(),
        source="memory",
        validity="structurally_verified",
        version_profile=profile,
        value=_frame_to_value(snapshot),
    )


def envelope_json(envelope: BattleObservationEnvelope) -> str:
    return json.dumps(envelope.to_json(), indent=2, ensure_ascii=False)
