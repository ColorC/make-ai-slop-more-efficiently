from __future__ import annotations

from pathlib import Path

from omnicompany.packages.services._core.material_wiki.adapters.markdown import (
    WikiDocument,
    WikiOperation,
    WikiVersion,
)
from omnicompany.packages.services._core.material_wiki.projections import (
    ProjectionContext,
    event_payload,
)
from omnicompany.runtime.agent_runtime.material_bridge import MaterialWikiProjectionReader
from omnicompany.runtime.agent_runtime.material_shadow import compare_material_wiki_shadow


class _Service:
    def __init__(self, document: WikiDocument) -> None:
        self.document = document

    def get(self, material_id: str) -> WikiDocument:
        assert material_id == self.document.material_id
        return self.document


def _canonical_document() -> tuple[WikiDocument, WikiOperation]:
    version = WikiVersion(
        version="v1",
        status="confirmed",
        body="this canonical body must not enter EventBus",
        sources=(),
        author_session="session:shadow",
        created_at="2026-08-14T00:00:00+00:00",
    )
    operation = WikiOperation(
        key="shadow-create",
        operation="create",
        version="v1",
        status="confirmed",
        current_version="v1",
        created_at="2026-08-14T00:00:00+00:00",
        author_session="session:shadow",
    )
    document = WikiDocument(
        material_id="material:shadow",
        project_id="omni",
        profile_id="omni.materials",
        title="Shadow source",
        canonical_ref="project:omni/wiki/materials/shadow.md",
        placement_path=("facts",),
        head_version="v1",
        current_version="v1",
        versions=(version,),
        operations=(operation,),
    )
    return document, operation


def test_material_event_is_a_matching_bodyless_shadow_of_canonical_read() -> None:
    document, operation = _canonical_document()
    canonical = MaterialWikiProjectionReader(_Service(document)).read(document.material_id)
    shadow = event_payload(
        ProjectionContext(
            mutation_id="mutation:shadow",
            document=document,
            operation=operation,
            canonical_path=Path("wiki/shadow.md"),
        )
    )

    comparison = compare_material_wiki_shadow(canonical, shadow)

    assert comparison.matches is True
    assert comparison.differences == ()
    assert canonical.content["body"] == "this canonical body must not enter EventBus"
    assert "body" not in shadow


def test_material_shadow_comparison_reports_projection_drift() -> None:
    document, _operation = _canonical_document()
    canonical = MaterialWikiProjectionReader(_Service(document)).read(document.material_id)

    comparison = compare_material_wiki_shadow(
        canonical,
        {
            "material_id": "material:shadow",
            "version": "v0",
            "body": "illegally copied",
        },
    )

    assert comparison.matches is False
    assert any(item.startswith("version:") for item in comparison.differences)
    assert "event projection contains canonical body" in comparison.differences
