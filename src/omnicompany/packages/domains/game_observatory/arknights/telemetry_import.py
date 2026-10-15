# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T02:35:03+08:00 type=telemetry-import-contract status=active
"""Safe, offline contracts for importing Arknights battle telemetry.

This module deliberately contains no emulator or process-reading capability.  It accepts
already-produced observations, preserves per-field provenance, validates the complete import
candidate, and only then atomically replaces the importer's current state.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Literal, Mapping

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .tournament_persistent_state import (
    AtomicStateStore,
    LocalStateBindingV1,
    PersistentStateError,
    require_local_state_store,
)

__all__ = [
    "AtomicBattleStateImporter",
    "BattleStateImportBundleV1",
    "DegradationLevel",
    "FieldProvenanceV1",
    "FieldQuality",
    "FixtureValidationReportV1",
    "ImportExpectationsV1",
    "ImportedBattleStateV1",
    "P0AcceptanceReportV1",
    "P0ComparisonV1",
    "P0_REQUIRED_FIELDS",
    "StateDeltaV1",
    "StateSnapshotV1",
    "TelemetryCapabilitiesV1",
    "TelemetryFieldV1",
    "TelemetryImportError",
    "TelemetryManifestV1",
    "TelemetrySource",
    "battle_state_import_bundle_schema",
    "evaluate_p0_acceptance",
    "materialize_import_bundle",
    "redact_sensitive_payload",
    "select_degradation_level",
    "validate_import_fixtures",
]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_FIELD_PATH_PATTERN = re.compile(r"^[a-z][a-z0-9_.\[\]-]{0,159}$")
_SENSITIVE_PATH_PARTS = {
    "access_token",
    "account_id",
    "authorization",
    "cookie",
    "device_id",
    "emulator_serial",
    "login",
    "password",
    "refresh_token",
    "session_token",
}


class TelemetryImportError(ValueError):
    """A bundle cannot be safely materialized under the requested expectations."""


class FieldQuality(str, Enum):
    EXACT = "exact"
    ESTIMATED = "estimated"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class TelemetrySource(str, Enum):
    AUTHORIZED_READ_ONLY = "authorized_read_only"
    OCR = "ocr"
    MAA = "maa"
    HYBRID = "hybrid"
    SIMULATOR_INFERENCE = "simulator_inference"
    UNKNOWN = "unknown"


class DegradationLevel(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class FieldProvenanceV1(BaseModel):
    """Evidence metadata without raw screenshots, paths, credentials, or identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    quality: FieldQuality
    source: TelemetrySource
    observed_at: AwareDatetime
    backend: str = Field(min_length=1, max_length=120)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_unknown_provenance(self) -> "FieldProvenanceV1":
        if self.quality is FieldQuality.UNKNOWN:
            if self.source is not TelemetrySource.UNKNOWN and not self.reason:
                raise ValueError("unknown provenance must explain why the source failed")
            if not self.reason:
                raise ValueError("unknown provenance must contain a reason")
        return self


class TelemetryFieldV1(BaseModel):
    """A dynamic field where unknown is structurally distinct from every real value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: JsonValue | None = None
    provenance: FieldProvenanceV1

    @model_validator(mode="after")
    def validate_value_quality_contract(self) -> "TelemetryFieldV1":
        if self.provenance.quality is FieldQuality.UNKNOWN:
            if self.value is not None:
                raise ValueError("unknown field must have value=null; unknown is not zero")
        elif self.value is None:
            raise ValueError("known field must contain a non-null value")
        return self


def _validate_field_mapping(
    values: Mapping[str, TelemetryFieldV1],
) -> Mapping[str, TelemetryFieldV1]:
    if not values:
        raise ValueError("telemetry field mapping cannot be empty")
    for path in values:
        if not _FIELD_PATH_PATTERN.fullmatch(path):
            raise ValueError(f"invalid telemetry field path: {path!r}")
        parts = set(path.split("."))
        if parts & _SENSITIVE_PATH_PARTS:
            raise ValueError(f"sensitive field is forbidden in telemetry: {path!r}")
    return values


class StateSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(default=0, ge=0)
    observed_at: AwareDatetime
    fields: dict[str, TelemetryFieldV1]

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, values: dict[str, TelemetryFieldV1]) -> dict[str, TelemetryFieldV1]:
        _validate_field_mapping(values)
        return values


class StateDeltaV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_sequence: int = Field(ge=0)
    sequence: int = Field(gt=0)
    observed_at: AwareDatetime
    changes: dict[str, TelemetryFieldV1]

    @field_validator("changes")
    @classmethod
    def validate_changes(cls, values: dict[str, TelemetryFieldV1]) -> dict[str, TelemetryFieldV1]:
        _validate_field_mapping(values)
        return values

    @model_validator(mode="after")
    def validate_sequence(self) -> "StateDeltaV1":
        if self.sequence != self.base_sequence + 1:
            raise ValueError("delta sequence must be exactly base_sequence + 1")
        return self


class TelemetryManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["omnicompany.arknights.battle-state-import-bundle.v1"] = Field(
        default="omnicompany.arknights.battle-state-import-bundle.v1",
        alias="schema",
    )
    bundle_id: str = Field(min_length=1, max_length=120)
    battle_session_id: str = Field(min_length=1, max_length=120)
    producer_version: str = Field(min_length=1, max_length=80)
    client_build: str = Field(min_length=1, max_length=120)
    emulator_build: str = Field(min_length=1, max_length=120)
    static_data_sha256: str = Field(pattern=_SHA256_PATTERN)
    captured_at: AwareDatetime
    degradation_level: DegradationLevel


class BattleStateImportBundleV1(BaseModel):
    """A full snapshot followed by gap-free, chronological deltas."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    manifest: TelemetryManifestV1
    snapshot: StateSnapshotV1
    deltas: tuple[StateDeltaV1, ...] = ()

    @model_validator(mode="after")
    def validate_ordered_deltas(self) -> "BattleStateImportBundleV1":
        sequence = self.snapshot.sequence
        observed_at = self.snapshot.observed_at
        for delta in self.deltas:
            if delta.base_sequence != sequence:
                raise ValueError("deltas must form one ordered, gap-free sequence")
            if delta.observed_at < observed_at:
                raise ValueError("delta timestamps must be monotonic")
            sequence = delta.sequence
            observed_at = delta.observed_at
        if self.manifest.captured_at < self.snapshot.observed_at:
            raise ValueError("manifest captured_at cannot precede the snapshot")
        if observed_at > self.manifest.captured_at:
            raise ValueError("manifest captured_at cannot precede the final delta")
        return self


P0_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "battle.lifecycle",
        "clock.battle_time_ms",
        "friendly.deployed",
        "friendly.redeploy_cooldowns",
        "friendly.skill_ready",
        "resources.current_cost",
        "resources.kills",
        "resources.life_points",
        "resources.total_enemies",
    }
)


class ImportExpectationsV1(BaseModel):
    """Accepted local capture formats checked before state replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_client_builds: frozenset[str] = Field(min_length=1)
    allowed_emulator_builds: frozenset[str] = Field(min_length=1)
    static_data_sha256: str = Field(pattern=_SHA256_PATTERN)
    allowed_degradation_levels: frozenset[DegradationLevel] = Field(min_length=1)
    allowed_sources: frozenset[TelemetrySource] = Field(min_length=1)
    required_known_fields: frozenset[str] = P0_REQUIRED_FIELDS
    accepted_qualities: frozenset[FieldQuality] = frozenset(
        {FieldQuality.EXACT, FieldQuality.ESTIMATED}
    )

    @classmethod
    def for_input_level(
        cls,
        *,
        allowed_client_builds: frozenset[str],
        allowed_emulator_builds: frozenset[str],
        static_data_sha256: str,
        memory_input_level: DegradationLevel,
        required_known_fields: frozenset[str] = P0_REQUIRED_FIELDS,
        accepted_qualities: frozenset[FieldQuality] = frozenset(
            {FieldQuality.EXACT, FieldQuality.ESTIMATED}
        ),
    ) -> "ImportExpectationsV1":
        """Build a source policy for one selected local input level."""

        sources = {
            DegradationLevel.L0: frozenset({TelemetrySource.AUTHORIZED_READ_ONLY}),
            DegradationLevel.L1: frozenset(
                {
                    TelemetrySource.AUTHORIZED_READ_ONLY,
                    TelemetrySource.OCR,
                    TelemetrySource.MAA,
                    TelemetrySource.HYBRID,
                }
            ),
            DegradationLevel.L2: frozenset({TelemetrySource.OCR, TelemetrySource.MAA}),
        }
        if memory_input_level not in sources:
            raise ValueError("tournament input level must be L0, L1, or L2")
        return cls(
            allowed_client_builds=allowed_client_builds,
            allowed_emulator_builds=allowed_emulator_builds,
            static_data_sha256=static_data_sha256,
            allowed_degradation_levels=frozenset({memory_input_level}),
            allowed_sources=sources[memory_input_level],
            required_known_fields=required_known_fields,
            accepted_qualities=accepted_qualities,
        )

    @field_validator("required_known_fields")
    @classmethod
    def validate_required_paths(cls, paths: frozenset[str]) -> frozenset[str]:
        for path in paths:
            if not _FIELD_PATH_PATTERN.fullmatch(path):
                raise ValueError(f"invalid required field path: {path!r}")
        return paths

    @field_validator("accepted_qualities")
    @classmethod
    def reject_unknown_as_accepted_quality(
        cls, qualities: frozenset[FieldQuality]
    ) -> frozenset[FieldQuality]:
        if FieldQuality.UNKNOWN in qualities:
            raise ValueError("unknown quality can never satisfy a required field")
        if not qualities:
            raise ValueError("at least one known quality must be accepted")
        return qualities

    @field_validator("allowed_sources")
    @classmethod
    def reject_unknown_as_allowed_source(
        cls, sources: frozenset[TelemetrySource]
    ) -> frozenset[TelemetrySource]:
        if TelemetrySource.UNKNOWN in sources:
            raise ValueError("unknown telemetry source cannot be authorized")
        return sources


class ImportedBattleStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_id: str
    battle_session_id: str
    static_data_sha256: str = Field(pattern=_SHA256_PATTERN)
    sequence: int = Field(ge=0)
    observed_at: AwareDatetime
    fields: dict[str, TelemetryFieldV1]
    state_sha256: str = Field(pattern=_SHA256_PATTERN)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_environment(
    bundle: BattleStateImportBundleV1, expectations: ImportExpectationsV1
) -> None:
    manifest = bundle.manifest
    if manifest.client_build not in expectations.allowed_client_builds:
        raise TelemetryImportError("client build is not in the accepted set")
    if manifest.emulator_build not in expectations.allowed_emulator_builds:
        raise TelemetryImportError("emulator build is not in the accepted set")
    if manifest.static_data_sha256 != expectations.static_data_sha256:
        raise TelemetryImportError("static data hash mismatch")
    if manifest.degradation_level not in expectations.allowed_degradation_levels:
        raise TelemetryImportError("telemetry degradation level is not authorized")

    observed_fields = list(bundle.snapshot.fields.items())
    for delta in bundle.deltas:
        observed_fields.extend(delta.changes.items())
    rejected_sources = sorted(
        path
        for path, field in observed_fields
        if field.provenance.quality is not FieldQuality.UNKNOWN
        and field.provenance.source not in expectations.allowed_sources
    )
    if rejected_sources:
        raise TelemetryImportError(
            "telemetry source is not authorized for fields: " + ", ".join(rejected_sources)
        )


def materialize_import_bundle(
    bundle: BattleStateImportBundleV1,
    expectations: ImportExpectationsV1,
) -> ImportedBattleStateV1:
    """Validate and materialize a candidate without mutating any importer state."""

    _validate_environment(bundle, expectations)
    fields = {path: value.model_copy(deep=True) for path, value in bundle.snapshot.fields.items()}
    sequence = bundle.snapshot.sequence
    observed_at: datetime = bundle.snapshot.observed_at
    for delta in bundle.deltas:
        fields.update({path: value.model_copy(deep=True) for path, value in delta.changes.items()})
        sequence = delta.sequence
        observed_at = delta.observed_at

    missing = sorted(expectations.required_known_fields - fields.keys())
    if missing:
        raise TelemetryImportError("required fields are missing: " + ", ".join(missing))

    rejected: list[str] = []
    for path in sorted(expectations.required_known_fields):
        quality = fields[path].provenance.quality
        if quality not in expectations.accepted_qualities:
            rejected.append(f"{path}={quality.value}")
    if rejected:
        raise TelemetryImportError(
            "required fields are unknown or below the accepted quality: " + ", ".join(rejected)
        )

    state_payload = {
        "bundle_id": bundle.manifest.bundle_id,
        "battle_session_id": bundle.manifest.battle_session_id,
        "static_data_sha256": bundle.manifest.static_data_sha256,
        "sequence": sequence,
        "observed_at": observed_at.isoformat(),
        "fields": {path: value.model_dump(mode="json") for path, value in sorted(fields.items())},
    }
    state_hash = hashlib.sha256(_canonical_json(state_payload).encode("utf-8")).hexdigest()
    return ImportedBattleStateV1(
        bundle_id=bundle.manifest.bundle_id,
        battle_session_id=bundle.manifest.battle_session_id,
        static_data_sha256=bundle.manifest.static_data_sha256,
        sequence=sequence,
        fields=fields,
        observed_at=observed_at,
        state_sha256=state_hash,
    )


class AtomicBattleStateImporter:
    """Atomic local importer with replay protection and optional SQLite state."""

    def __init__(
        self,
        expectations: ImportExpectationsV1,
        *,
        state_store: AtomicStateStore | None = None,
        state_id: str = "local-tournament",
        run_id: str = "default",
    ) -> None:
        self._expectations = expectations
        self._lock = threading.Lock()
        self._binding = LocalStateBindingV1(
            tournament_id=state_id,
            run_scope=run_id,
        )
        try:
            self._state_store = require_local_state_store(
                state_store,
                persistent=state_store is not None,
                fixture_id="telemetry-importer-memory",
            )
            existing = self._state_store.read("telemetry-importer", self._binding)
            if existing is not None:
                self._decode_persistent_state(existing)
        except PersistentStateError as exc:
            raise TelemetryImportError("telemetry persistent state is unavailable") from exc

    @property
    def expectations(self) -> ImportExpectationsV1:
        return self._expectations.model_copy(deep=True)

    def import_bundle(self, bundle: BattleStateImportBundleV1) -> ImportedBattleStateV1:
        """Validate and atomically install one OCR/MAA observation bundle."""
        candidate = materialize_import_bundle(bundle, self._expectations)
        return self._commit(candidate)

    def _commit(
        self,
        candidate: ImportedBattleStateV1,
    ) -> ImportedBattleStateV1:
        with self._lock:

            def update(raw: dict[str, Any] | None) -> dict[str, Any]:
                state = self._decode_persistent_state(raw or self._empty_persistent_state())
                retired = set(state["retired_battle_sessions"])
                current_raw = state["current"]
                current = (
                    ImportedBattleStateV1.model_validate(current_raw)
                    if current_raw is not None
                    else None
                )
                if current is not None:
                    same_session = candidate.battle_session_id == current.battle_session_id
                    if same_session and candidate.sequence <= current.sequence:
                        raise TelemetryImportError(
                            "telemetry sequence rollback or replay is forbidden"
                        )
                    if not same_session:
                        if candidate.battle_session_id in retired:
                            raise TelemetryImportError("retired battle session cannot be restored")
                        retired.add(current.battle_session_id)
                return {
                    "schema": "game-observatory.arknights-telemetry-import-state.v1",
                    "current": candidate.model_dump(mode="json"),
                    "retired_battle_sessions": sorted(retired),
                }

            try:
                self._state_store.transact("telemetry-importer", self._binding, update)
            except TelemetryImportError:
                raise
            except PersistentStateError as exc:
                raise TelemetryImportError("telemetry persistent state transaction failed") from exc
        return candidate.model_copy(deep=True)

    @property
    def current(self) -> ImportedBattleStateV1 | None:
        with self._lock:
            try:
                raw = self._state_store.read("telemetry-importer", self._binding)
                if raw is None:
                    return None
                current = self._decode_persistent_state(raw)["current"]
                return (
                    ImportedBattleStateV1.model_validate(current).model_copy(deep=True)
                    if current is not None
                    else None
                )
            except PersistentStateError as exc:
                raise TelemetryImportError("telemetry persistent state read failed") from exc

    @staticmethod
    def _empty_persistent_state() -> dict[str, Any]:
        return {
            "schema": "game-observatory.arknights-telemetry-import-state.v1",
            "current": None,
            "retired_battle_sessions": [],
        }

    def _decode_persistent_state(self, raw: dict[str, Any]) -> dict[str, Any]:
        if (
            set(raw)
            != {
                "schema",
                "current",
                "retired_battle_sessions",
            }
            or raw.get("schema") != "game-observatory.arknights-telemetry-import-state.v1"
        ):
            raise PersistentStateError("telemetry state payload schema is corrupt")
        retired = raw.get("retired_battle_sessions")
        if (
            not isinstance(retired, list)
            or not all(isinstance(item, str) and item for item in retired)
            or len(set(retired)) != len(retired)
        ):
            raise PersistentStateError("telemetry replay state is corrupt")
        current_raw = raw.get("current")
        if current_raw is not None:
            try:
                current = ImportedBattleStateV1.model_validate(current_raw)
            except Exception as exc:
                raise PersistentStateError("telemetry current state is corrupt") from exc
            if current.static_data_sha256 != self._expectations.static_data_sha256:
                raise PersistentStateError("telemetry current state crosses static data binding")
        return raw


class TelemetryCapabilitiesV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized_read_only_available: bool = False
    read_only_version_match: bool = False
    read_only_p0_verified: bool = False
    ocr_available: bool = False
    maa_available: bool = False
    partial_observation_available: bool = False


def select_degradation_level(capabilities: TelemetryCapabilitiesV1) -> DegradationLevel:
    """Choose the highest safe input level; a version mismatch can never yield L0/L1."""

    safe_read_only = (
        capabilities.authorized_read_only_available and capabilities.read_only_version_match
    )
    visual_available = capabilities.ocr_available or capabilities.maa_available
    if safe_read_only and capabilities.read_only_p0_verified:
        return DegradationLevel.L0
    if safe_read_only and visual_available:
        return DegradationLevel.L1
    if visual_available:
        return DegradationLevel.L2
    if capabilities.partial_observation_available:
        return DegradationLevel.L3
    raise TelemetryImportError("no safe telemetry level is available")


class P0ComparisonV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_path: str
    expected: JsonValue
    observed: TelemetryFieldV1
    schema_valid: bool = True
    time_error_ms: float | None = Field(default=None, ge=0)

    @field_validator("field_path")
    @classmethod
    def validate_field_path(cls, path: str) -> str:
        if not _FIELD_PATH_PATTERN.fullmatch(path):
            raise ValueError(f"invalid P0 field path: {path!r}")
        return path


class P0AcceptanceReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    samples: int = Field(ge=0)
    correct: int = Field(ge=0)
    unknown: int = Field(ge=0)
    silent_errors: int = Field(ge=0)
    schema_failures: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    time_error_p95_ms: float | None = Field(default=None, ge=0)
    passes_p0_gate: bool


def _nearest_rank_p95(values: list[float]) -> float | None:
    if not values:
        return None
    values.sort()
    return values[max(0, math.ceil(0.95 * len(values)) - 1)]


def evaluate_p0_acceptance(
    comparisons: Iterable[P0ComparisonV1],
    *,
    accuracy_threshold: float = 0.995,
    max_time_error_p95_ms: float = 200.0,
) -> P0AcceptanceReportV1:
    """Compute the M2 gate without treating unknown observations as correct."""

    rows = list(comparisons)
    correct = 0
    unknown = 0
    silent_errors = 0
    schema_failures = 0
    time_errors: list[float] = []
    for row in rows:
        if not row.schema_valid:
            schema_failures += 1
        quality = row.observed.provenance.quality
        if quality is FieldQuality.UNKNOWN:
            unknown += 1
        elif row.observed.value == row.expected:
            correct += 1
        else:
            silent_errors += 1
        if row.time_error_ms is not None:
            time_errors.append(row.time_error_ms)

    accuracy = correct / len(rows) if rows else 0.0
    p95 = _nearest_rank_p95(time_errors)
    passes = bool(
        rows
        and accuracy >= accuracy_threshold
        and silent_errors == 0
        and schema_failures == 0
        and p95 is not None
        and p95 <= max_time_error_p95_ms
    )
    return P0AcceptanceReportV1(
        samples=len(rows),
        correct=correct,
        unknown=unknown,
        silent_errors=silent_errors,
        schema_failures=schema_failures,
        accuracy=accuracy,
        time_error_p95_ms=p95,
        passes_p0_gate=passes,
    )


class FixtureValidationReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_fixtures: int = Field(ge=1)
    fixtures_seen: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    state_hashes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    ready: bool


def validate_import_fixtures(
    fixtures: Iterable[BattleStateImportBundleV1 | Mapping[str, Any]],
    expectations: ImportExpectationsV1,
    *,
    required_fixtures: int = 100,
) -> FixtureValidationReportV1:
    """Extensible S6 entry point; production readiness defaults to 100 fixtures."""

    accepted = 0
    state_hashes: list[str] = []
    errors: list[str] = []
    count = 0
    for count, raw in enumerate(fixtures, start=1):
        try:
            bundle = (
                raw
                if isinstance(raw, BattleStateImportBundleV1)
                else BattleStateImportBundleV1.model_validate(raw)
            )
            first = materialize_import_bundle(bundle, expectations)
            second = materialize_import_bundle(bundle, expectations)
            if first.state_sha256 != second.state_sha256:
                raise TelemetryImportError("fixture materialization is not deterministic")
            accepted += 1
            state_hashes.append(first.state_sha256)
        except Exception as exc:  # noqa: BLE001 - fixture reports need all failures
            errors.append(f"fixture[{count - 1}]: {type(exc).__name__}: {exc}")
    rejected = count - accepted
    return FixtureValidationReportV1(
        required_fixtures=required_fixtures,
        fixtures_seen=count,
        accepted=accepted,
        rejected=rejected,
        state_hashes=tuple(state_hashes),
        errors=tuple(errors),
        ready=count >= required_fixtures and rejected == 0,
    )


_SECRET_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "password",
    "refresh_token",
    "secret",
    "session_token",
}
_IDENTIFIER_KEYS = {"account_id", "device_id", "emulator_serial", "uid"}


def redact_sensitive_payload(value: JsonValue, *, salt: str) -> JsonValue:
    """Return a deterministic audit-safe copy; raw secrets and identifiers never survive."""

    def redact(item: JsonValue) -> JsonValue:
        if isinstance(item, dict):
            output: dict[str, JsonValue] = {}
            for key, child in item.items():
                normalized = key.lower()
                if normalized in _SECRET_KEYS:
                    output[key] = "<redacted>"
                elif normalized in _IDENTIFIER_KEYS:
                    encoded = _canonical_json(child).encode("utf-8")
                    digest = hashlib.sha256(salt.encode("utf-8") + b":" + encoded).hexdigest()
                    output[key] = f"sha256:{digest}"
                else:
                    output[key] = redact(child)
            return output
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    return redact(value)


def battle_state_import_bundle_schema() -> dict[str, Any]:
    return BattleStateImportBundleV1.model_json_schema(by_alias=True)
