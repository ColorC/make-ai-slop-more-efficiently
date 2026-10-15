"""Public static types for Material Core graph payloads."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class MaterialSourceRef(TypedDict):
    kind: str
    ref: str
    selector: NotRequired[str]


class MaterialRevisionRef(TypedDict):
    provider: str
    object: str
    revision: str


class MaterialVersionRef(TypedDict):
    material_id: str
    revision_ref: MaterialRevisionRef


class MaterialRepresentation(TypedDict):
    id: str
    media_type: str
    role: str
    source_ref: MaterialSourceRef
    title: NotRequired[str]
    renderer_hint: NotRequired[str]
    content: NotRequired[str]
    content_format: NotRequired[str]


class MaterialViewMember(TypedDict):
    material_id: str
    representation_id: NotRequired[str]
    group: NotRequired[str]
    order: NotRequired[float]


class Material(TypedDict):
    id: str
    title: str
    labels: list[str]
    revision_ref: MaterialRevisionRef
    representations: list[MaterialRepresentation]
    summary: NotRequired[str]
    relationship: NotRequired[dict[str, str]]
    view: NotRequired[dict[str, object]]
    formal_domain: NotRequired[dict[str, object]]
    formalization: NotRequired[dict[str, object]]
    metadata: NotRequired[dict[str, object]]


class MaterialGraph(TypedDict):
    contract_version: str
    graph_id: str
    generated_at: str
    materials: list[Material]
    active_view_id: NotRequired[str]
