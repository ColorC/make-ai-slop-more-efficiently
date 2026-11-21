"""Production wiring for formal domains on the OmniCompany workspace."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .authority import AuthorityIndex
from .collaboration import CollaborationContextRule
from .formalization import FormalizationStore
from .governance import CompositeArtifactInspector, DomainRule, GovernanceEngine
from .registry import DomainRegistry
from .service import FormalizationService


class DomainGovernanceRouter:
    """Select product governance without changing the shared service contract."""

    def __init__(self, default: GovernanceEngine, *, by_adapter: dict[str, Any]) -> None:
        self.default = default
        self.by_adapter = dict(by_adapter)

    def evaluate(
        self,
        domain: dict[str, Any],
        record: dict[str, Any],
        *,
        authority: Any | None = None,
    ):
        formal_domain = domain.get("formal_domain", domain)
        adapter_ref = formal_domain.get("adapter_ref", {})
        adapter = (
            self.by_adapter.get(adapter_ref.get("ref"))
            if isinstance(adapter_ref, dict)
            else None
        )
        return (adapter or self.default).evaluate(
            domain,
            record,
            authority=authority,
        )


def build_formalization_service(
    *,
    omnicompany_root: str | Path,
    workspace_root: str | Path | None = None,
    registry_path: str | Path | None = None,
    records_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    finding_archive: Any | None = None,
    domain_rules: Iterable[DomainRule] = (),
    demoproject_p4_workspace_root: str | Path | None = None,
    demoproject_p4_runner: Any | None = None,
    demoproject_additional_inspectors: dict[str, Any] | None = None,
) -> FormalizationService:
    """Build one service shared by CLI and the existing Dashboard process.

    File/Git and Material Wiki are adapters above Material Core.  Missing
    optional facilities remain uninspectable and therefore fail governance.
    """

    repo = Path(omnicompany_root).resolve()
    workspace = Path(workspace_root or repo.parent).resolve()
    registry = DomainRegistry(
        Path(registry_path or repo / "config" / "material_domains.yaml"),
        workspace_root=workspace,
    )
    inspector = CompositeArtifactInspector.with_defaults(repo)
    rules = [*domain_rules, CollaborationContextRule()]

    manifest = repo / "config" / "material_wiki" / "profiles.yaml"
    if manifest.is_file():
        from ..material_wiki.formal_domains import (
            MaterialWikiArtifactInspector,
            MaterialWikiIdentityInputRule,
            RequiredMarkdownConceptRule,
        )
        from ..material_wiki.runtime import MaterialWikiRuntime

        wiki_runtime = MaterialWikiRuntime(omnicompany_root=repo, manifest_path=manifest)
        wiki_inspector = MaterialWikiArtifactInspector(wiki_runtime)
        for kind in ("material-wiki-document", "material-wiki-view"):
            inspector.by_kind[kind] = wiki_inspector
        inspector.by_provider["material-wiki"] = wiki_inspector
        rules.extend(
            (
                RequiredMarkdownConceptRule(wiki_runtime),
                MaterialWikiIdentityInputRule(),
            )
        )

    default_governance = GovernanceEngine(
        inspector=inspector,
        finding_archive=finding_archive,
        domain_rules=rules,
    )

    governance = DomainGovernanceRouter(default_governance, by_adapter={})

    return FormalizationService(
        registry,
        FormalizationStore(Path(records_path or repo / "data" / "material" / "formalizations")),
        AuthorityIndex(
            Path(
                authority_path
                or repo
                / "data"
                / "services"
                / "material_domains"
                / "authority-index.json"
            )
        ),
        governance,
    )


__all__ = ["DomainGovernanceRouter", "build_formalization_service"]
