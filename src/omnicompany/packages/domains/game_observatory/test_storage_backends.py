from __future__ import annotations

import pytest

from omnicompany.packages.domains.game_observatory.runtime import GameObservatory
from omnicompany.packages.domains.game_observatory.storage_backends import (
    MinioArtifactProjection,
    MinioSettings,
    PostgresCanonicalProjection,
    StorageBackendError,
)


def test_minio_settings_fail_closed_and_object_names_are_content_addressed(monkeypatch):
    for name in (
        "GAME_OBSERVATORY_MINIO_ENDPOINT",
        "GAME_OBSERVATORY_MINIO_ACCESS_KEY",
        "GAME_OBSERVATORY_MINIO_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(StorageBackendError, match="missing MinIO settings"):
        MinioSettings.from_env()
    assert (
        MinioArtifactProjection.object_name("a" * 64, ".PNG")
        == f"sha256/aa/{'a' * 64}.png"
    )


def test_postgres_projection_contains_canonical_objects_relations_and_trace(tmp_path):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    facility.store.append_event("run.fixture", "observe", {"screen": "hero"})

    rows = PostgresCanonicalProjection._object_rows(facility.store)
    relations = PostgresCanonicalProjection._relations(facility.store)

    object_types = {row[0] for row in rows}
    assert {"report", "source", "flow", "claim", "source_snapshot", "trace_event"} <= object_types
    assert any(item[2] == "has_source" for item in relations)
    assert any(item[2] == "provenance" for item in relations)
    assert facility.store.list_trace_events("run.fixture")[0]["payload"] == {"screen": "hero"}