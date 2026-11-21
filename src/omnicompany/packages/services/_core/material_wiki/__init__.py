# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=package status=active
# [OMNI] summary="材料 Wiki 通用合同与项目 profile 入口；只定义身份、版本、来源、关系和分类指针，不存放项目正文"
# [OMNI] material_id="material:core.material_wiki.package"
"""Material Wiki contract and project profile loading."""

from .profiles import load_profile_from_wiki, load_profile_manifest
from .formal_domains import (
    MaterialWikiArtifactInspector,
    MaterialWikiFormalDomainError,
    MaterialWikiIdentityInputRule,
    RequiredMarkdownConceptRule,
)
from .formalization_adapter import MaterialWikiFormalizationAdapter
from .material_graph import build_material_wiki_graph
from .schema import (
    MaterialRelations,
    MaterialSourceRef,
    MaterialStatusDefinition,
    MaterialTaxonomyNode,
    MaterialVersionRef,
    MaterialWikiContractError,
    MaterialWikiEntry,
    MaterialWikiProfile,
    ProfileRegistration,
    ProfileSource,
    TaxonomySourceKind,
)

__all__ = [
    "MaterialRelations",
    "MaterialSourceRef",
    "MaterialStatusDefinition",
    "MaterialTaxonomyNode",
    "MaterialVersionRef",
    "MaterialWikiContractError",
    "MaterialWikiArtifactInspector",
    "MaterialWikiFormalDomainError",
    "MaterialWikiFormalizationAdapter",
    "build_material_wiki_graph",
    "MaterialWikiIdentityInputRule",
    "MaterialWikiEntry",
    "MaterialWikiProfile",
    "ProfileRegistration",
    "ProfileSource",
    "TaxonomySourceKind",
    "RequiredMarkdownConceptRule",
    "load_profile_from_wiki",
    "load_profile_manifest",
]
