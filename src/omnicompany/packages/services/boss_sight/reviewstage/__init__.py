# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.reviewstage.__init__.py"
"""BOSS SIGHT 审阅台 — 块 4. 5 类 material × 4 级分级 + 评论 + 批注 + 圈选元素."""

from .store import (
    AnnotationKind,
    Comment,
    Annotation,
    Material,
    MaterialKind,
    MaterialStore,
    MaterialTier,
    MaterialStatus,
)
from .material_types import (
    registered_review_kinds,
    registered_review_tiers,
    review_material_tags,
)
from .capabilities import (
    ReviewContext,
    ReviewReference,
    ReviewSubmissionIntent,
    capability_catalog,
    resolve_review_submission,
)
from .context_spine import (
    build_material_context_spine,
    canonical_review_ref,
)

__all__ = [
    "AnnotationKind",
    "Comment",
    "Annotation",
    "Material",
    "MaterialKind",
    "MaterialStore",
    "MaterialTier",
    "MaterialStatus",
    "registered_review_kinds",
    "registered_review_tiers",
    "review_material_tags",
    "ReviewContext",
    "ReviewReference",
    "ReviewSubmissionIntent",
    "capability_catalog",
    "resolve_review_submission",
    "build_material_context_spine",
    "canonical_review_ref",
]
