"""Material Wiki adapter that prepares and records exact formal-domain members."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from ..material_domains.service import FormalizationService
from .formal_domains import MaterialWikiFormalDomainError, _resolver_for
from .runtime import MaterialWikiRuntime


def _revision(provider: str, object_id: str, revision: str) -> dict[str, str]:
    return {"provider": provider, "object": object_id, "revision": revision}


class MaterialWikiFormalizationAdapter:
    """Formalize the exact current Wiki members declared by one domain profile."""

    def __init__(self, runtime: MaterialWikiRuntime, service: FormalizationService) -> None:
        self.runtime = runtime
        self.service = service
        self._resolver = _resolver_for(runtime)

    @staticmethod
    def _domain_material(domain: dict[str, Any]) -> dict[str, Any]:
        graph_path = Path(domain["graph_path"])
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        return next(item for item in graph["materials"] if item["id"] == domain["material_id"])

    def prepare(
        self,
        domain_id: str,
        *,
        occurred_at: str,
        execution: dict[str, str],
        content: dict[str, str],
        maintenance: dict[str, str],
        idempotency_prefix: str,
    ) -> list[dict[str, Any]]:
        domain = self.service.registry.resolve(domain_id)
        domain_material = self._domain_material(domain)
        fd = domain_material["formal_domain"]
        extensions = fd.get("extensions", {})
        view_ids = set(extensions.get("view_material_ids", [])) if isinstance(extensions, dict) else set()
        prepared: list[dict[str, Any]] = []

        for scope in fd["authority_scopes"]:
            material_id = scope["object"]
            resolved = self._resolver.resolve(material_id)
            if resolved is None:
                raise MaterialWikiFormalDomainError(
                    f"formal domain member {material_id!r} is absent from Material Wiki"
                )
            _wiki_adapter, document = resolved
            version = document.current_version
            if not version:
                raise MaterialWikiFormalDomainError(
                    f"formal domain member {material_id!r} has no current Wiki version"
                )
            material_revision = _revision("material-wiki", material_id, version)
            domain_revision = deepcopy(domain["domain_ref"]["revision_ref"])
            record_id = f"formalization:{domain_id}:{material_id}:{version}"
            source = {"kind": "material-wiki", "ref": f"material:{material_id}@{version}"}
            graph: dict[str, Any] = {
                "contract_version": "0.2.0",
                "graph_id": record_id,
                "generated_at": occurred_at,
                "materials": [
                    {
                        "id": record_id,
                        "title": f"{document.title}进入正式域",
                        "labels": ["formalization", "material-wiki"],
                        "revision_ref": _revision("material-domain-record", record_id, "v1"),
                        "representations": [
                            {
                                "id": "record-source",
                                "media_type": "application/json",
                                "role": "primary",
                                "source_ref": source,
                            }
                        ],
                        "formalization": {
                            "operation": "enter",
                            "outcome": "succeeded",
                            "domain_ref": {
                                "material_id": domain_id,
                                "revision_ref": domain_revision,
                            },
                            "input_material": {
                                "material_id": material_id,
                                "revision_ref": material_revision,
                            },
                            "artifacts": [
                                {
                                    "kind": (
                                        "material-wiki-view"
                                        if material_id in view_ids
                                        else "material-wiki-document"
                                    ),
                                    "ref": document.canonical_ref,
                                    "revision_ref": material_revision,
                                }
                            ],
                            "sources": [source],
                            "responsibility": {
                                "execution": execution,
                                "content": content,
                                "maintenance": maintenance,
                            },
                            "rule_results": [
                                {
                                    "rule_id": "formal-domain-preflight",
                                    "status": "passed",
                                    "evidence": [source],
                                }
                            ],
                            "authority_effect": {
                                "action": "establish",
                                "scopes": [deepcopy(scope)],
                            },
                            "occurred_at": occurred_at,
                            "idempotency_key": (
                                f"{idempotency_prefix}:{domain_id}:"
                                f"{domain_revision['revision']}:{material_id}:{version}:enter"
                            ),
                        },
                    }
                ],
            }
            report = self.service.check(graph)
            if report.accepted:
                formalization = graph["materials"][0]["formalization"]
                formalization["rule_results"] = [
                    {
                        "rule_id": finding.rule_id,
                        "status": "passed",
                        "evidence": finding.evidence or [source],
                        "note": finding.message,
                    }
                    for finding in report.findings
                ]
                report = self.service.check(graph)
            prepared.append(
                {
                    "material_id": material_id,
                    "graph": graph,
                    "report": report,
                }
            )
        return prepared

    def execute(
        self,
        domain_id: str,
        *,
        occurred_at: str,
        execution: dict[str, str],
        content: dict[str, str],
        maintenance: dict[str, str],
        idempotency_prefix: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        prepared = self.prepare(
            domain_id,
            occurred_at=occurred_at,
            execution=execution,
            content=content,
            maintenance=maintenance,
            idempotency_prefix=idempotency_prefix,
        )
        accepted = all(item["report"].accepted for item in prepared)
        if not accepted or dry_run:
            return {
                "domain_id": domain_id,
                "dry_run": dry_run,
                "accepted": accepted,
                "items": prepared,
            }
        for item in prepared:
            item["result"] = self.service.record(item["graph"])
        return {
            "domain_id": domain_id,
            "dry_run": False,
            "accepted": True,
            "items": prepared,
        }


__all__ = ["MaterialWikiFormalizationAdapter"]
