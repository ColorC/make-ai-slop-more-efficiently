"""Language-neutral Material Core v0.2 Python binding.

The core deliberately validates graph structure only.  Governance decisions are
owned by callers and are not represented by this module.
"""

from .core import (
    MATERIAL_CORE_VERSION,
    SUPPORTED_MATERIAL_CORE_VERSIONS,
    MaterialGraphIssue,
    MaterialGraphValidationError,
    assert_material_graph,
    canonical_manifest,
    material_map,
    resolve_view,
    slice_graph_for_view,
    validate_material_graph,
)
from .types import (
    Material,
    MaterialGraph,
    MaterialRepresentation,
    MaterialRevisionRef,
    MaterialSourceRef,
    MaterialVersionRef,
    MaterialViewMember,
)

__all__ = [
    "MATERIAL_CORE_VERSION",
    "SUPPORTED_MATERIAL_CORE_VERSIONS",
    "MaterialGraphIssue",
    "MaterialGraphValidationError",
    "Material",
    "MaterialGraph",
    "MaterialRepresentation",
    "MaterialRevisionRef",
    "MaterialSourceRef",
    "MaterialVersionRef",
    "MaterialViewMember",
    "assert_material_graph",
    "canonical_manifest",
    "material_map",
    "resolve_view",
    "slice_graph_for_view",
    "validate_material_graph",
]
