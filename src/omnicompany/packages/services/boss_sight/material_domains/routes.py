"""HTTP API for registered formal domains and their authority projection."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from omnicompany.packages.services._core.material_domains import (
    AuthorityConflictError,
    DomainNotFoundError,
    DomainRegistryError,
    FormalizationConflictError,
    FormalizationService,
)
from omnicompany.packages.services._core.material_domains.runtime import (
    build_formalization_service,
)

SCHEMA_VERSION = 1
material_domains_router = APIRouter(prefix="/api/boss-sight/material-domains", tags=["material-domains"])


class FormalizationRecordInput(BaseModel):
    graph: dict[str, Any]
    idempotency_key: str = Field(min_length=1)
    source: list[str] | dict[str, Any]


def _payload(*, status: str, **values: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status": status, **values}


@lru_cache(maxsize=1)
def get_formalization_service() -> FormalizationService:
    """Build the production service; tests may replace this factory."""
    from omnicompany.core.config import omni_workspace_root

    omnicompany_root = Path(omni_workspace_root()).resolve()
    return build_formalization_service(omnicompany_root=omnicompany_root)


def _record_material(graph: dict[str, Any]) -> dict[str, Any]:
    materials = graph.get("materials")
    if not isinstance(materials, list):
        raise ValueError("graph must contain materials")
    candidates = [item for item in materials if isinstance(item, dict) and isinstance(item.get("formalization"), dict)]
    if len(candidates) != 1:
        raise ValueError("formalization graph must contain exactly one formalization material")
    return candidates[0]


def _validate_record_input(input_: FormalizationRecordInput) -> None:
    material = _record_material(input_.graph)
    formal = material["formalization"]
    if formal.get("idempotency_key") != input_.idempotency_key:
        raise ValueError("idempotency_key must match graph formalization")
    sources = formal.get("sources")
    if not isinstance(sources, list):
        raise ValueError("source must match a graph formalization source")
    if isinstance(input_.source, dict):
        matches = input_.source in sources
    else:
        available = {f"{item.get('kind')}={item.get('ref')}" for item in sources if isinstance(item, dict)}
        matches = bool(input_.source) and all(value in available for value in input_.source)
    if not matches:
        raise ValueError("source must match a graph formalization source")


def _status_error(exc: Exception) -> tuple[int, dict[str, Any]]:
    if isinstance(exc, DomainNotFoundError):
        return 404, _payload(status="not_found", error=str(exc))
    if isinstance(exc, FormalizationConflictError):
        return 409, _payload(status="conflict", error=str(exc))
    if isinstance(exc, (AuthorityConflictError, DomainRegistryError, ValueError, TypeError)):
        return 400, _payload(status="invalid", error=str(exc))
    return 500, _payload(status="error", error="material domain operation failed")


def _execute(operation: Any) -> Any:
    try:
        return operation()
    except Exception as exc:  # noqa: BLE001
        code, body = _status_error(exc)
        return JSONResponse(status_code=code, content=body)


def _domain_payload(domain: dict[str, Any]) -> dict[str, Any]:
    return {key: domain[key] for key in ("material_id", "graph_ref", "domain_ref", "formal_domain")}


def _stored_payload(stored: dict[str, Any] | None) -> dict[str, Any] | None:
    if stored is None:
        return None
    material = _record_material(stored["graph"])
    return {"created": stored["created"], "record_id": material["id"], "record_ref": {"material_id": material["id"], "revision_ref": material["revision_ref"]}, "graph": stored["graph"]}


@material_domains_router.get("/")
def list_domains() -> dict[str, Any]:
    return _execute(lambda: _payload(status="ok", domains=[_domain_payload(item) for item in get_formalization_service().registry.list()]))


@material_domains_router.get("/formalizations")
def list_formalizations() -> dict[str, Any]:
    return _execute(lambda: _payload(status="ok", formalizations=[{"record_id": _record_material(graph)["id"], "graph": graph} for graph in get_formalization_service().store.list()]))


@material_domains_router.get("/formalizations/{record_id}")
def get_formalization(record_id: str) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        for graph in get_formalization_service().store.list():
            if _record_material(graph)["id"] == record_id:
                return _payload(status="ok", record_id=record_id, graph=graph)
        raise DomainNotFoundError(f"unknown formalization record: {record_id}")
    return _execute(operation)


@material_domains_router.post("/formalizations/check")
def check_formalization(graph: dict[str, Any]) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        report = get_formalization_service().check(graph)
        return _payload(status="accepted" if report.accepted else "rejected", report=report.as_dict())
    return _execute(operation)


@material_domains_router.post("/formalizations/record")
def record_formalization(input_: FormalizationRecordInput) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        _validate_record_input(input_)
        result = get_formalization_service().record(input_.graph)
        report, stored = result["report"], _stored_payload(result["stored"])
        status = "recorded" if stored and stored["created"] else "idempotent" if stored else "rejected"
        return _payload(status=status, report=report.as_dict() if report else None, stored=stored, authority=result["authority"])
    return _execute(operation)


@material_domains_router.get("/authority")
def get_authority(object: str = Query(min_length=1), purpose: str = Query(min_length=1), scope: str = Query(min_length=1)) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        authority = get_formalization_service().authority.get(
            {"object": object, "purpose": purpose, "scope": scope},
            include_conflict=True,
        )
        return _payload(**authority)

    return _execute(operation)


@material_domains_router.get("/authority/history")
def authority_history(object: str | None = None, purpose: str | None = None, scope: str | None = None) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        values = (object, purpose, scope)
        if any(value is not None for value in values) and not all(values):
            raise ValueError("object, purpose and scope must be supplied together")
        selector = {"object": object, "purpose": purpose, "scope": scope} if all(values) else None
        return _payload(status="ok", history=get_formalization_service().authority.history(selector))
    return _execute(operation)


@material_domains_router.post("/authority/rebuild")
def rebuild_authority() -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        projection = get_formalization_service().rebuild_authority()
        conflict = any(len(entry.get("current", [])) != 1 for entry in projection.get("entries", {}).values())
        return _payload(status="conflict" if conflict else "rebuilt", projection=projection)
    return _execute(operation)


@material_domains_router.get("/{domain_id}")
def get_domain(domain_id: str) -> dict[str, Any]:
    return _execute(lambda: _payload(status="ok", domain=_domain_payload(get_formalization_service().registry.context(domain_id))))
