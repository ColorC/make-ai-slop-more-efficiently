"""Compare canonical Material reads with disposable domain-event projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .material_bridge import MaterialProjection


@dataclass(frozen=True, slots=True)
class MaterialShadowComparison:
    matches: bool
    differences: tuple[str, ...]


def compare_material_wiki_shadow(
    canonical: MaterialProjection,
    event_payload: Mapping[str, Any],
) -> MaterialShadowComparison:
    """Compare stable metadata while proving the event carries no canonical body."""
    expected = {
        "material_id": canonical.material_ref,
        "version": canonical.revision,
        "project_id": canonical.content.get("project_id"),
        "profile_id": canonical.content.get("profile_id"),
        "title": canonical.content.get("title"),
        "canonical_ref": canonical.content.get("canonical_ref"),
        "status": canonical.content.get("status"),
    }
    differences = [
        f"{key}: canonical={value!r} event={event_payload.get(key)!r}"
        for key, value in expected.items()
        if event_payload.get(key) != value
    ]
    if "body" in event_payload:
        differences.append("event projection contains canonical body")
    return MaterialShadowComparison(not differences, tuple(differences))


__all__ = ["MaterialShadowComparison", "compare_material_wiki_shadow"]
