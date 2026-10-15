"""Cohesive Spark formal-domain governance adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..governance import (
    ArtifactInspector,
    CompositeArtifactInspector,
    GovernanceEngine,
)
from .p4 import (
    IgameP4ArtifactChecker,
    IgameP4ArtifactInspector,
    IgameP4Runner,
    P4_ARTIFACT_KINDS,
)
from .rules import (
    SparkArtifactRequirementsRule,
    SparkCopySourceRule,
    SparkP4VerifiabilityRule,
    SparkRuntimeInterfaceObservationRule,
    SparkUserReviewRule,
)


class SparkFormalDomainAdapter:
    """Build governance for Spark without importing Spark or P4 production code.

    Non-P4 artifacts, especially complete game packages and build receipts, have
    no optimistic filesystem fallback. Their authoritative inspector must be
    injected by the owning build/release facility; otherwise governance blocks.
    """

    def __init__(
        self,
        p4_workspace_root: Path,
        *,
        p4_runner: IgameP4Runner | None = None,
        additional_inspectors: dict[str, ArtifactInspector] | None = None,
        finding_archive: Any | None = None,
    ) -> None:
        checker = IgameP4ArtifactChecker(p4_workspace_root, runner=p4_runner)
        p4_inspector = IgameP4ArtifactInspector(checker)
        by_kind: dict[str, ArtifactInspector] = {
            kind: p4_inspector for kind in P4_ARTIFACT_KINDS
        }
        by_kind.update(additional_inspectors or {})
        self.checker = checker
        self.engine = GovernanceEngine(
            inspector=CompositeArtifactInspector(by_kind=by_kind),
            finding_archive=finding_archive,
            domain_rules=[
                SparkArtifactRequirementsRule(),
                SparkP4VerifiabilityRule(checker),
                SparkRuntimeInterfaceObservationRule(),
                SparkCopySourceRule(),
                SparkUserReviewRule(),
            ],
        )

    def evaluate(
        self,
        domain: dict[str, Any],
        record: dict[str, Any],
        *,
        authority: Any | None = None,
    ):
        return self.engine.evaluate(domain, record, authority=authority)


def build_spark_governance_engine(
    p4_workspace_root: Path,
    *,
    p4_runner: IgameP4Runner | None = None,
    additional_inspectors: dict[str, ArtifactInspector] | None = None,
    finding_archive: Any | None = None,
) -> GovernanceEngine:
    return SparkFormalDomainAdapter(
        p4_workspace_root,
        p4_runner=p4_runner,
        additional_inspectors=additional_inspectors,
        finding_archive=finding_archive,
    ).engine
