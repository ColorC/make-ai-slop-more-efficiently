from __future__ import annotations

import pytest

from omnicompany.packages.domains.game_observatory.editorial import (
    EditorialError,
    EditorialService,
    PatchConflict,
)
from omnicompany.packages.domains.game_observatory.models import ReportPatchOperation
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory


def test_patch_annotation_and_incremental_compile_preserve_history(tmp_path):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    report = facility.store.get_report("afk-journey-hero-upgrade")
    assert report is not None
    base_revision = facility.store.current_revision(report.id)
    service = EditorialService(facility.store)
    annotation = service.annotate(
        report.id,
        object_id=report.flow[0].id,
        author="researcher",
        body="复核入口按钮命名。",
        kind="question",
        source_ids=[report.flow[0].source_ids[0]],
    )
    assert annotation.status == "active"
    patch = service.propose_patch(
        report.id,
        base_revision=base_revision,
        author="researcher",
        note="把入口描述写得更精确",
        operations=[
            ReportPatchOperation(
                op="replace",
                target_kind="flow",
                target_id=report.flow[0].id,
                field="description",
                value="教程定位 MainView 的英雄入口按钮，并在开放系统按钮后引导点击。",
            )
        ],
    )
    applied = service.apply_patch(patch.id, reviewer="reviewer")
    assert applied.status == "applied"
    assert applied.applied_revision == base_revision + 1
    revised = facility.store.get_report(report.id)
    assert revised.flow[0].description.startswith("教程定位 MainView")
    assert facility.store.get_revision(report.id, base_revision).flow[0].description != revised.flow[0].description

    build = facility.compile_public()
    assert report.slug in build["compiled"]
    assert build["changed_sections"][report.slug] == ["flow"]
    repeated = facility.compile_public()
    assert repeated["compiled"] == []
    assert set(repeated["skipped"]) == {item.slug for item in facility.store.list_reports()}

    resolved = service.resolve_annotation(annotation.id, reviewer="reviewer")
    assert resolved.status == "resolved"


def test_patch_conflict_and_unprovenanced_add_fail_closed(tmp_path):
    facility = GameObservatory(tmp_path)
    facility.bootstrap()
    report = facility.store.get_report("minecraft-stone-pickaxe")
    assert report is not None
    service = EditorialService(facility.store)
    stale = service.propose_patch(
        report.id,
        base_revision=99,
        author="author",
        note="stale",
        operations=[
            ReportPatchOperation(
                op="replace",
                target_kind="report",
                field="summary",
                value=report.summary + " stale",
            )
        ],
    )
    with pytest.raises(PatchConflict):
        service.apply_patch(stale.id, reviewer="reviewer")

    invalid = service.propose_patch(
        report.id,
        base_revision=facility.store.current_revision(report.id),
        author="author",
        note="unsupported claim",
        operations=[
            ReportPatchOperation(
                op="add",
                target_kind="claim",
                value={
                    "id": "claim.unsupported",
                    "kind": "analyst_interpretation",
                    "statement": "unsupported",
                    "review_status": "reviewed",
                },
            )
        ],
    )
    with pytest.raises(ValueError, match="no source, artifact, or run evidence"):
        service.apply_patch(invalid.id, reviewer="reviewer")
    assert facility.store.get_report_patch(invalid.id).status == "proposed"

    with pytest.raises(EditorialError, match="target not found"):
        service.annotate(
            report.id,
            object_id="missing.object",
            author="author",
            body="no target",
        )