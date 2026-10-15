"""Machine-checkable Agent reports prepared before formal-domain recording.

Reports are deliberately read-only preparation artifacts: validation never writes
formalization records or authority projections.  The embedded graph is retained
unchanged so a valid report can be handed directly to ``FormalizationService``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..material_core import assert_material_graph
from .governance import GovernanceEngine
from .registry import DomainRegistry
from .service import FormalizationService


class AgentFormalizationReportError(ValueError):
    """An Agent's preparation report is incomplete or conflicts with its domain."""


_REQUIRED = (
    "action",
    "target_domain",
    "input_material",
    "formal_artifacts",
    "base_revision",
    "sources",
    "responsibility",
    "rule_results",
    "evidence",
    "authority_impact",
    "remaining_unknown",
    "formalization_graph",
)


@dataclass(frozen=True)
class AgentFormalizationReport:
    """A lossless, reviewable envelope around one formalization graph."""

    payload: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentFormalizationReport":
        if value.get("schema_version") != 1:
            raise AgentFormalizationReportError("report.schema_version must be 1")
        missing = [name for name in _REQUIRED if name not in value]
        if missing:
            raise AgentFormalizationReportError("report missing required fields: " + ", ".join(missing))
        if not isinstance(value.get("remaining_unknown"), list):
            raise AgentFormalizationReportError("report.remaining_unknown must be an array")
        graph = value.get("formalization_graph")
        try:
            validated = assert_material_graph(graph)
            material = FormalizationService._material(validated)
        except (TypeError, ValueError) as exc:
            raise AgentFormalizationReportError(f"invalid formalization_graph: {exc}") from exc
        formal = material["formalization"]
        expected = {
            "action": formal["operation"],
            "target_domain": formal["domain_ref"],
            "input_material": formal["input_material"],
            "formal_artifacts": formal["artifacts"],
            "base_revision": formal.get("base_revision_ref"),
            "sources": formal["sources"],
            "responsibility": formal["responsibility"],
            "rule_results": formal["rule_results"],
            "authority_impact": formal["authority_effect"],
        }
        for field, expected_value in expected.items():
            if value.get(field) != expected_value:
                raise AgentFormalizationReportError(f"report.{field} must exactly match formalization_graph")
        if expected["base_revision"] is None:
            raise AgentFormalizationReportError("report.base_revision is required")
        if not isinstance(value.get("evidence"), list) or not value["evidence"]:
            raise AgentFormalizationReportError("report.evidence must be a non-empty array")
        if any(result.get("status") != "passed" for result in formal["rule_results"]):
            raise AgentFormalizationReportError("report rejects failed or waived rule results")
        rule_ids = {result.get("rule_id") for result in formal["rule_results"]}
        missing_common = set(GovernanceEngine.RULE_IDS) - rule_ids
        if missing_common:
            raise AgentFormalizationReportError(
                "report missing common governance results: " + ", ".join(sorted(missing_common))
            )
        return cls(dict(value))

    def to_formalization_input(self) -> dict[str, Any]:
        """Return the original graph unchanged for ``check`` or ``record``."""
        return self.payload["formalization_graph"]

    def validate_domain(self, registry: DomainRegistry) -> dict[str, Any]:
        graph = self.to_formalization_input()
        material = FormalizationService._material(graph)
        domain_ref = material["formalization"]["domain_ref"]
        domain = registry.resolve(domain_ref["material_id"])
        if domain_ref != domain["domain_ref"]:
            raise AgentFormalizationReportError("report target_domain must match the registered exact domain revision")
        return domain

    def check(self, service: FormalizationService) -> Any:
        """Run the real governance facility without persisting anything."""
        self.validate_domain(service.registry)
        report = service.check(self.to_formalization_input())
        if not report.accepted:
            raise AgentFormalizationReportError("formalization report is rejected by governance")
        return report


__all__ = ["AgentFormalizationReport", "AgentFormalizationReportError"]
