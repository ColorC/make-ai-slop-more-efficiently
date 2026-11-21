"""Transactional orchestration for formalization checking and recording."""
from __future__ import annotations

from typing import Any

from ..material_core import assert_material_graph
from .authority import AuthorityIndex
from .formalization import FormalizationStore
from .governance import GovernanceEngine, GovernanceReport
from .registry import DomainRegistry


class FormalizationService:
    """Coordinates validation, governance, append-only storage and projection recovery."""

    def __init__(self, registry: DomainRegistry, store: FormalizationStore, authority: AuthorityIndex, governance: GovernanceEngine) -> None:
        self.registry, self.store, self.authority, self.governance = registry, store, authority, governance

    @staticmethod
    def _material(graph: object) -> dict[str, Any]:
        validated = assert_material_graph(graph)
        candidates = [material for material in validated["materials"] if isinstance(material.get("formalization"), dict)]
        if len(candidates) != 1:
            raise ValueError("formalization graph must contain exactly one formalization material")
        return candidates[0]

    def check(self, graph: object) -> GovernanceReport:
        record = self._material(graph)
        domain_id = record["formalization"]["domain_ref"]["material_id"]
        domain = self.registry.resolve(domain_id)
        return self.governance.evaluate(domain, record, authority=self.authority)

    def record(self, graph: object) -> dict[str, Any]:
        """Persist a record only after an accepted check; repair projection on any write failure.

        Authority is a rebuildable projection.  It is changed first, then a failed body
        write is rolled back by rebuilding solely from the durable append-only records.
        """
        material = self._material(graph)
        formal = material["formalization"]
        existing = self.store.find_by_idempotency_key(formal["idempotency_key"])
        if existing is not None:
            stored = self.store.record(graph)  # validates identical content, raising on conflict
            projection = self.rebuild_authority()
            return {"report": None, "stored": stored, "authority": {"status": "idempotent", "projection": projection}}
        report = self.check(graph)
        if formal["outcome"] == "rejected":
            stored = self.store.record(graph)
            return {"report": report, "stored": stored, "authority": {"status": "unchanged"}}
        if not report.accepted:
            return {"report": report, "stored": None, "authority": {"status": "unchanged"}}
        try:
            authority_result = self.authority.apply(material)
            stored = self.store.record(graph)
            # Keep the live projection byte-for-byte aligned with its only
            # recovery path.  Incremental application order is process order;
            # rebuild order is the durable record order and is canonical.
            authority_result["projection"] = self.rebuild_authority()
        except Exception:
            self.authority.rebuild([self._material(item) for item in self.store.list()])
            raise
        return {"report": report, "stored": stored, "authority": authority_result}

    def rebuild_authority(self) -> dict[str, Any]:
        return self.authority.rebuild([self._material(item) for item in self.store.list()])
