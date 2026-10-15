"""Spark formal-domain profiles, P4 inspection, and product governance rules."""

from .adapter import SparkFormalDomainAdapter, build_spark_governance_engine
from .p4 import (
    IgameP4ArtifactChecker,
    IgameP4ArtifactInspector,
    IgameP4Runner,
    P4ArtifactCheck,
    P4_ARTIFACT_KINDS,
    SubprocessIgameP4Runner,
)
from .rules import (
    COPY_SOURCE_KINDS,
    SparkArtifactRequirementsRule,
    SparkCopySourceRule,
    SparkP4VerifiabilityRule,
    SparkRuntimeInterfaceObservationRule,
    SparkUserReviewRule,
)

__all__ = [
    "COPY_SOURCE_KINDS",
    "IgameP4ArtifactChecker",
    "IgameP4ArtifactInspector",
    "IgameP4Runner",
    "P4ArtifactCheck",
    "P4_ARTIFACT_KINDS",
    "SparkArtifactRequirementsRule",
    "SparkCopySourceRule",
    "SparkFormalDomainAdapter",
    "SparkP4VerifiabilityRule",
    "SparkRuntimeInterfaceObservationRule",
    "SparkUserReviewRule",
    "SubprocessIgameP4Runner",
    "build_spark_governance_engine",
]
