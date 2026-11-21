"""Formal domain registration, governance, and authority projections.

This package deliberately sits above :mod:`material_core`: Core validates graph
shape; these services resolve pointers and make governance decisions.
"""

from .authority import AuthorityConflictError, AuthorityIndex
from .agent_reports import AgentFormalizationReport, AgentFormalizationReportError
from .collaboration import CollaborationContextRule
from .formalization import FormalizationConflictError, FormalizationStore
from .governance import (
    ArtifactInspection,
    CompositeArtifactInspector,
    DomainRule,
    FileArtifactInspector,
    GitArtifactInspector,
    GovernanceEngine,
    GovernanceFinding,
    GovernanceReport,
)
from .registry import DomainNotFoundError, DomainRegistry, DomainRegistryError
from .runtime import build_formalization_service
from .service import FormalizationService

__all__ = [
    "ArtifactInspection",
    "AgentFormalizationReport",
    "AgentFormalizationReportError",
    "CompositeArtifactInspector",
    "CollaborationContextRule",
    "AuthorityConflictError",
    "AuthorityIndex",
    "DomainNotFoundError",
    "DomainRegistry",
    "DomainRegistryError",
    "DomainRule",
    "FileArtifactInspector",
    "FormalizationConflictError",
    "FormalizationStore",
    "FormalizationService",
    "GitArtifactInspector",
    "GovernanceEngine",
    "GovernanceFinding",
    "GovernanceReport",
    "build_formalization_service",
]
