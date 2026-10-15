# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T02:35:03+08:00 type=simulator-import-bridge status=active
"""Fail-closed bridge from local OCR/MAA telemetry to simulator state."""

from __future__ import annotations

from collections.abc import Mapping

from ..telemetry_import import (
    BattleStateImportBundleV1,
    FieldQuality,
    ImportExpectationsV1,
    ImportedBattleStateV1,
    TelemetryImportError,
    materialize_import_bundle,
)
from .models import BattleStatus, InitialState

__all__ = [
    "SIM_INITIAL_STATE_FIELD",
    "initial_state_from_import_bundle",
]

SIM_INITIAL_STATE_FIELD = "sim.initial_state"


def initial_state_from_import_bundle(
    bundle: BattleStateImportBundleV1,
    expectations: ImportExpectationsV1,
) -> InitialState:
    """Offline-fixture helper that extracts one canonical simulator state.

    The full simulator state is carried as one provenance-bearing field so a
    partial delta can never be mistaken for a complete state.  Redundant P0
    values are cross-checked to expose producer mapping conflicts.
    """

    imported = materialize_import_bundle(bundle, expectations)
    return _initial_state_from_imported(
        imported,
        accepted_qualities=expectations.accepted_qualities,
    )


def _initial_state_from_imported(
    imported: ImportedBattleStateV1,
    *,
    accepted_qualities: frozenset[FieldQuality],
) -> InitialState:
    field = imported.fields.get(SIM_INITIAL_STATE_FIELD)
    if field is None:
        raise TelemetryImportError(f"required field is missing: {SIM_INITIAL_STATE_FIELD}")
    if field.provenance.quality not in accepted_qualities:
        raise TelemetryImportError("simulator initial state is unknown or below accepted quality")
    if not isinstance(field.value, Mapping):
        raise TelemetryImportError("simulator initial state must be a JSON object")
    try:
        initial = InitialState.model_validate(field.value)
    except ValueError as exc:
        raise TelemetryImportError("simulator initial state failed schema validation") from exc
    if initial.content_hash != imported.static_data_sha256:
        raise TelemetryImportError(
            "simulator state content hash does not match telemetry static data"
        )

    expected_values = {
        "resources.current_cost": initial.cost,
        "resources.kills": initial.kills,
        "resources.life_points": initial.life_points,
        "battle.lifecycle": initial.status.value,
    }
    conflicts = [
        path
        for path, expected in expected_values.items()
        if path not in imported.fields or imported.fields[path].value != expected
    ]
    if conflicts:
        raise TelemetryImportError(
            "simulator state conflicts with P0 telemetry: " + ", ".join(conflicts)
        )
    if initial.status is not BattleStatus.RUNNING:
        raise TelemetryImportError("only a running battle may seed a live forecast branch")
    return initial

