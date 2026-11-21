# [OMNI] origin=codex domain=dashboard/boss_sight/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="材料 Wiki HTTP API：profile、列表、正文、历史、关系、建立、修订、关联、可恢复归档与恢复均委托唯一 MaterialWikiService"
# [OMNI] why="StudioReader 和后续项目界面需要真实本体回写，不能继续只改 ReviewStage 投影或 Vilo 草稿旁路"
# [OMNI] tags=material-wiki,http,studio-reader,canonical
# [OMNI] material_id="material:dashboard.boss_sight.material_wiki.routes.py"
"""Canonical Material Wiki API used by StudioReader and external clients."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from omnicompany.packages.services._core.material_wiki.adapters.markdown import (
    MaterialEntryExistsError,
    MaterialEntryNotFoundError,
    MaterialIdempotencyConflictError,
    MaterialVersionConflictError,
    MaterialWikiStorageError,
    WikiDocument,
    WikiVersion,
)
from omnicompany.packages.services._core.material_wiki.agent_context import (
    build_agent_context,
)
from omnicompany.packages.services._core.material_wiki.runtime import MaterialWikiRuntime
from omnicompany.packages.services._core.material_wiki.report_refs import (
    MaterialReportRef,
    render_report,
    report_view,
)
from omnicompany.packages.services._core.material_wiki.material_graph import (
    build_material_wiki_graph,
)
from omnicompany.packages.services._core.material_wiki.schema import (
    MaterialSourceRef,
    MaterialVersionRef,
    MaterialWikiContractError,
    MaterialWikiProfile,
)


material_wiki_router = APIRouter(
    prefix="/api/boss-sight/material-wiki",
    tags=["material-wiki"],
)


class SourceInput(BaseModel):
    kind: str
    ref: str
    note: str | None = None


class CreateInput(BaseModel):
    title: str
    placement_path: list[str] = Field(min_length=1)
    body: str
    status: str
    sources: list[SourceInput] = Field(min_length=1)
    idempotency_key: str
    material_id: str | None = None
    version: str = "v1"
    dry_run: bool = False


class ReviseInput(BaseModel):
    body: str
    status: str
    sources: list[SourceInput] = Field(min_length=1)
    expected_version: str
    idempotency_key: str
    version: str | None = None
    dry_run: bool = False


class RetitleInput(BaseModel):
    title: str
    sources: list[SourceInput] = Field(min_length=1)
    expected_version: str
    idempotency_key: str
    dry_run: bool = False


class ArchiveInput(BaseModel):
    target_version: str
    expected_version: str
    sources: list[SourceInput] = Field(min_length=1)
    idempotency_key: str
    dry_run: bool = False


class RestoreInput(ArchiveInput):
    status: str


class LinkInput(ArchiveInput):
    relation: str
    related_material_id: str
    related_version: str


class BatchArchiveItem(ArchiveInput):
    material_id: str


class BatchArchiveInput(BaseModel):
    items: list[BatchArchiveItem] = Field(min_length=1)


class ReportRefInput(BaseModel):
    material_id: str
    version_policy: str = "latest"
    version: str | None = None


class ReportRenderInput(BaseModel):
    title: str
    narration: str = ""
    material_refs: list[ReportRefInput] = Field(min_length=1)


@lru_cache(maxsize=1)
def get_runtime() -> MaterialWikiRuntime:
    from omnicompany.core.config import omni_workspace_root

    return MaterialWikiRuntime(omnicompany_root=omni_workspace_root())


def _sources(values: list[SourceInput]) -> tuple[MaterialSourceRef, ...]:
    return tuple(MaterialSourceRef(item.kind, item.ref, item.note) for item in values)


def _profile_node(node: Any) -> dict[str, Any]:
    return {
        "id": node.id,
        "label": node.label,
        "allow_dynamic_children": node.allow_dynamic_children,
        "children": [_profile_node(child) for child in node.children],
    }


def _profile_payload(profile: MaterialWikiProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "project_id": profile.project_id,
        "schema_version": profile.schema_version,
        "source": {"kind": profile.source.kind.value, "ref": profile.source.ref},
        "taxonomy": [_profile_node(node) for node in profile.taxonomy],
        "statuses": [asdict(status) for status in profile.statuses],
        "default_status": profile.default_status,
        "locked_depth": profile.locked_depth,
        "dynamic_from_depth": profile.dynamic_from_depth,
        "max_depth": profile.max_depth,
    }


def _relation_payload(version: WikiVersion) -> dict[str, Any]:
    return asdict(version.relations)


def _version_payload(version: WikiVersion, *, include_body: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": version.version,
        "status": version.status,
        "created_at": version.created_at,
        "sources": [asdict(source) for source in version.sources],
        "relations": _relation_payload(version),
        "author_session": version.author_session,
        "prior_status": version.prior_status,
    }
    if include_body:
        result["body"] = version.body
    return result


def _stable_links(project_id: str, material_id: str, canonical_ref: str) -> dict[str, str]:
    encoded_project = quote(project_id, safe="")
    encoded_id = quote(material_id, safe="")
    if project_id in {"walker", "omni-product-family"}:
        view = (
            f"/?open_type=project&open_id={encoded_project}"
            f"&open_facet=wiki:{encoded_id}"
        )
    else:
        view = (
            f"/?open_type=studio_reader&open_id={encoded_project}"
            f"&open_facet=material-wiki:{encoded_id}"
        )
    return {
        "canonical_ref": canonical_ref,
        "api": f"/api/boss-sight/material-wiki/projects/{encoded_project}/materials/{encoded_id}",
        # Compatibility field for clients created before the project Wiki merge.
        "studio": view,
        "view": view,
    }


def _document_payload(
    document: WikiDocument,
    *,
    include_body: bool,
    selected_version: str | None = None,
) -> dict[str, Any]:
    versions = document.versions
    if selected_version:
        versions = (document.version(selected_version),)
    return {
        "material_id": document.material_id,
        "project_id": document.project_id,
        "profile_id": document.profile_id,
        "title": document.title,
        "placement_path": list(document.placement_path),
        "head_version": document.head_version,
        "current_version": document.current_version,
        "versions": [_version_payload(item, include_body=include_body) for item in versions],
        "links": _stable_links(document.project_id, document.material_id, document.canonical_ref),
    }


def _summary_payload(
    document: WikiDocument,
    *,
    path: Path,
    status_definition: Any,
    review_state: str | None = None,
) -> dict[str, Any]:
    """Small directory row; version history and bodies belong to the detail route."""
    display = document.current or document.head
    return {
        "material_id": document.material_id,
        "project_id": document.project_id,
        "profile_id": document.profile_id,
        "title": document.title,
        "placement_path": list(document.placement_path),
        "head_version": document.head_version,
        "current_version": document.current_version,
        "status": display.status,
        "approved": bool(status_definition and status_definition.approved),
        "terminal": bool(status_definition and status_definition.terminal),
        "preview": display.body[:240],
        "version_count": len(document.versions),
        "canonical_path": path.as_posix(),
        # 派生字段: 画布提审卡的裁决状态(submission.state), 无对应卡时为 None。
        "review_state": review_state,
        "links": _stable_links(document.project_id, document.material_id, document.canonical_ref),
    }


def _review_states_by_wiki_id() -> dict[str, str]:
    """画布提审卡 material.extra.wiki.material_id → 裁决状态(submission.state)。

    裁决状态只存在画布卡上, 不在 wiki 目录文件里, 列表投影按卡反查(参照
    _pinned_submissions 的 review_store.iter_submission_cards 用法)。同一 wiki
    条目被多次提审时取 submittedAt 最新的一张卡。
    """
    from omnicompany.packages.services._core import review_store

    latest: dict[str, tuple[str, str]] = {}
    for hit in review_store.iter_submission_cards(include_archived=False):
        card = hit.get("card") or {}
        material = card.get("material") or {}
        wiki = (material.get("extra") or {}).get("wiki") or {}
        material_id = str(wiki.get("material_id") or "")
        if not material_id:
            continue
        submission = hit.get("submission") or {}
        state = str(submission.get("state") or "unreviewed")
        submitted_at = str(submission.get("submittedAt") or "")
        existing = latest.get(material_id)
        if existing is None or submitted_at >= existing[0]:
            latest[material_id] = (submitted_at, state)
    return {material_id: state for material_id, (_at, state) in latest.items()}


def _review_states_best_effort() -> dict[str, str]:
    """canvas-store 不可达时降级为空映射: wiki 列表本体不因裁决投影失败而 500。"""
    try:
        return _review_states_by_wiki_id()
    except Exception:  # noqa: BLE001 - 派生字段失败不阻断目录
        return {}


def _status_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MaterialEntryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            MaterialEntryExistsError,
            MaterialVersionConflictError,
            MaterialIdempotencyConflictError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (MaterialWikiContractError, MaterialWikiStorageError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Material Wiki operation failed")


def _execute(function: Any) -> Any:
    try:
        return function()
    except Exception as exc:  # noqa: BLE001 - centralized API boundary
        raise _status_error(exc) from exc


def _project_signature(project_id: str) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for path, _document in get_runtime().documents(project_id):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((path.as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(rows))


@material_wiki_router.get("/projects")
def list_projects() -> dict[str, Any]:
    runtime = get_runtime()
    projects: list[dict[str, Any]] = []
    for registration in runtime.registrations():
        try:
            profile = runtime.profile(registration.project_id)
            projects.append(
                {
                    "project_id": registration.project_id,
                    "profile_id": registration.profile_id,
                    "available": True,
                    "taxonomy_roots": len(profile.taxonomy),
                }
            )
        except (MaterialWikiContractError, OSError) as exc:
            projects.append(
                {
                    "project_id": registration.project_id,
                    "profile_id": registration.profile_id,
                    "available": False,
                    "error": str(exc),
                }
            )
    return {"projects": projects}


@material_wiki_router.get("/projects/{project_id}/profile")
def get_profile(project_id: str) -> dict[str, Any]:
    return _execute(lambda: _profile_payload(get_runtime().profile(project_id)))


@material_wiki_router.get("/projects/{project_id}/agent-context")
def get_agent_context(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    include_terminal: bool = Query(default=False),
) -> dict[str, Any]:
    return _execute(
        lambda: build_agent_context(
            get_runtime(),
            project_id,
            offset=offset,
            limit=limit,
            include_terminal=include_terminal,
        )
    )


@material_wiki_router.get("/projects/{project_id}/events")
async def material_events(project_id: str) -> StreamingResponse:
    """Push directory changes; the Wiki has no manual refresh control."""
    _execute(lambda: get_runtime().profile(project_id))

    async def stream():
        previous = _project_signature(project_id)
        yield "event: ready\ndata: {}\n\n"
        ticks = 0
        while True:
            await asyncio.sleep(2)
            current = _project_signature(project_id)
            if current != previous:
                previous = current
                payload = json.dumps({"documents": len(current)}, ensure_ascii=False)
                yield f"event: changed\ndata: {payload}\n\n"
            ticks += 1
            if ticks % 8 == 0:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@material_wiki_router.get("/projects/{project_id}/materials")
def list_materials(
    project_id: str,
    status: str | None = Query(default=None),
    unapproved_only: bool = Query(default=False),
    include_terminal: bool = Query(default=False),
    query: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        runtime = get_runtime()
        profile = runtime.profile(project_id)
        status_by_id = {item.id: item for item in profile.statuses}
        root_order = {node.id: index for index, node in enumerate(profile.taxonomy)}
        child_order = {
            (root.id, child.id): index
            for root in profile.taxonomy
            for index, child in enumerate(root.children)
        }
        wanted = {item.strip() for item in (status or "").split(",") if item.strip()}
        needle = (query or "").strip().casefold()
        review_states = _review_states_best_effort()
        entries: list[dict[str, Any]] = []
        for path, document in runtime.documents(project_id):
            display = document.current or document.head
            definition = status_by_id.get(display.status)
            if wanted and display.status not in wanted:
                continue
            if unapproved_only and (definition is None or definition.approved or not definition.current):
                continue
            if not include_terminal and definition and definition.terminal:
                continue
            if needle and needle not in f"{document.material_id} {document.title} {display.body}".casefold():
                continue
            entries.append(
                _summary_payload(
                    document,
                    path=path,
                    status_definition=definition,
                    review_state=review_states.get(document.material_id),
                )
            )
        entries.sort(
            key=lambda item: (
                root_order.get(item["placement_path"][0], len(root_order)),
                child_order.get(
                    tuple(item["placement_path"][:2]),
                    len(child_order),
                ),
                item["placement_path"][2:],
                item["title"],
                item["material_id"],
            )
        )
        total = len(entries)
        page = entries[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "project_id": project_id,
            "count": len(page),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
            "materials": page,
        }

    return _execute(operation)


PINNED_SUBMISSION_LIMIT = 8


def _pinned_submissions(project_id: str, session: str | None) -> list[dict[str, object]]:
    """该会话画布里、项目匹配的提审卡(最新在前), 供 wiki 图 root 置顶组。"""
    if not session:
        return []
    from omnicompany.packages.services._core import review_store

    pinned: list[dict[str, object]] = []
    for hit in review_store.iter_submission_cards(include_archived=False):
        card = hit.get("card") or {}
        material = card.get("material") or {}
        if str(material.get("project") or "") != project_id:
            continue
        if card.get("kind") == "group":
            continue
        submission = hit.get("submission") or {}
        pinned.append({
            "canvas": str(hit.get("canvas") or ""),
            "session_id": str(hit.get("session_id") or ""),
            "card_id": str(card.get("id") or ""),
            "title": str(card.get("title") or card.get("id") or "未命名提交"),
            "state": str(submission.get("state") or "unreviewed"),
            "submitted_at": str(submission.get("submittedAt") or ""),
            "reason": str(submission["reason"]) if submission.get("reason") else None,
        })
    pinned.sort(key=lambda item: str(item["submitted_at"]), reverse=True)
    return pinned[:PINNED_SUBMISSION_LIMIT]


@material_wiki_router.get("/projects/{project_id}/graph")
def material_graph(
    project_id: str,
    session: str | None = Query(default=None, description="会话画布 id: 给出则该会话的提审卡置顶进 root 视图"),
) -> dict[str, object]:
    """Rebuildable Material Core read projection; canonical Markdown stays external."""
    return _execute(
        lambda: build_material_wiki_graph(
            project_id,
            get_runtime().profile(project_id),
            get_runtime().documents(project_id),
            pinned_submissions=_pinned_submissions(project_id, session),
        )
    )


@material_wiki_router.get("/projects/{project_id}/materials/{material_id}")
def get_material(
    project_id: str,
    material_id: str,
    version: str | None = Query(default=None),
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        runtime = get_runtime()
        _path, document, resolution = runtime.adapter(project_id).locate_with_resolution(material_id)
        payload = _document_payload(
            document,
            include_body=True,
            selected_version=version,
        )
        if resolution:
            payload["requested_material_id"] = resolution.requested_material_id
            payload["resolved_alias"] = {
                "target_material_id": resolution.target_material_id,
                "anchor": resolution.anchor,
                "reason": resolution.reason,
            }
        return payload

    return _execute(operation)


@material_wiki_router.get("/projects/{project_id}/materials/{material_id}/history")
def get_history(project_id: str, material_id: str) -> dict[str, Any]:
    return _execute(
        lambda: _document_payload(
            get_runtime().service(project_id).get(material_id),
            include_body=True,
        )
    )


@material_wiki_router.get("/projects/{project_id}/materials/{material_id}/relations")
def get_relations(project_id: str, material_id: str) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        document = get_runtime().service(project_id).get(material_id)
        return {
            "material_id": material_id,
            "versions": [
                {"version": item.version, "relations": _relation_payload(item)}
                for item in document.versions
            ],
        }

    return _execute(operation)


@material_wiki_router.post("/projects/{project_id}/materials")
def create_material(project_id: str, body: CreateInput) -> dict[str, Any]:
    return _execute(
        lambda: asdict(
            get_runtime().service(project_id).create(
                title=body.title,
                placement_path=body.placement_path,
                body=body.body,
                status=body.status,
                sources=_sources(body.sources),
                idempotency_key=body.idempotency_key,
                material_id=body.material_id,
                version=body.version,
                dry_run=body.dry_run,
            )
        )
    )


@material_wiki_router.post("/projects/{project_id}/materials/{material_id}/revisions")
def revise_material(project_id: str, material_id: str, body: ReviseInput) -> dict[str, Any]:
    return _execute(
        lambda: asdict(
            get_runtime().service(project_id).revise(
                material_id,
                body=body.body,
                status=body.status,
                sources=_sources(body.sources),
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
                version=body.version,
                dry_run=body.dry_run,
            )
        )
    )


@material_wiki_router.post("/projects/{project_id}/materials/{material_id}/retitle")
def retitle_material(project_id: str, material_id: str, body: RetitleInput) -> dict[str, Any]:
    return _execute(
        lambda: asdict(
            get_runtime().service(project_id).retitle(
                material_id,
                title=body.title,
                sources=_sources(body.sources),
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
                dry_run=body.dry_run,
            )
        )
    )


@material_wiki_router.post("/projects/{project_id}/materials/{material_id}/relations")
def link_material(project_id: str, material_id: str, body: LinkInput) -> dict[str, Any]:
    return _execute(
        lambda: asdict(
            get_runtime().service(project_id).link(
                material_id,
                target_version=body.target_version,
                relation=body.relation,
                related=MaterialVersionRef(body.related_material_id, body.related_version),
                sources=_sources(body.sources),
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
                dry_run=body.dry_run,
            )
        )
    )


@material_wiki_router.post("/projects/{project_id}/materials/{material_id}/archive")
def archive_material(project_id: str, material_id: str, body: ArchiveInput) -> dict[str, Any]:
    """Recoverable archive: rejected is terminal, while prior_status permits restore."""
    return _execute(
        lambda: asdict(
            get_runtime().service(project_id).reject(
                material_id,
                target_version=body.target_version,
                sources=_sources(body.sources),
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
                dry_run=body.dry_run,
            )
        )
    )


@material_wiki_router.post("/projects/{project_id}/materials/{material_id}/restore")
def restore_material(project_id: str, material_id: str, body: RestoreInput) -> dict[str, Any]:
    return _execute(
        lambda: asdict(
            get_runtime().service(project_id).restore(
                material_id,
                target_version=body.target_version,
                status=body.status,
                sources=_sources(body.sources),
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
                dry_run=body.dry_run,
            )
        )
    )


@material_wiki_router.post("/projects/{project_id}/batch-archive")
def batch_archive(project_id: str, body: BatchArchiveInput) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        service = get_runtime().service(project_id)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for item in body.items:
            material_id = item.material_id
            try:
                receipt = service.reject(
                    material_id,
                    target_version=item.target_version,
                    sources=_sources(item.sources),
                    expected_version=item.expected_version,
                    idempotency_key=item.idempotency_key,
                    dry_run=item.dry_run,
                )
                results.append(asdict(receipt))
            except Exception as exc:  # noqa: BLE001 - per-item recoverable receipt
                errors.append({"material_id": material_id, "error": str(exc)})
        return {"ok": not errors, "results": results, "errors": errors}

    return _execute(operation)


@material_wiki_router.post("/projects/{project_id}/rebuild")
def rebuild_project(project_id: str) -> dict[str, Any]:
    """Recreate disposable indexes, events and review views from canonical bodies."""
    return _execute(lambda: asdict(get_runtime().rebuild(project_id)))


@material_wiki_router.post("/projects/{project_id}/reports/materialize")
def materialize_report_paragraph(project_id: str, body: CreateInput) -> dict[str, Any]:
    """Place reusable report text in the Wiki and return its replacement reference."""

    def operation() -> dict[str, Any]:
        receipt = get_runtime().service(project_id).create(
            title=body.title,
            placement_path=body.placement_path,
            body=body.body,
            status=body.status,
            sources=_sources(body.sources),
            idempotency_key=body.idempotency_key,
            material_id=body.material_id,
            version=body.version,
            dry_run=body.dry_run,
        )
        return {
            "receipt": asdict(receipt),
            "material_ref": {
                "material_id": receipt.material_id,
                "version_policy": "latest",
            },
        }

    return _execute(operation)


@material_wiki_router.post("/projects/{project_id}/reports/render")
def render_report_view(project_id: str, body: ReportRenderInput) -> dict[str, Any]:
    """Render a report by reading referenced canonical bodies at request time."""

    def operation() -> dict[str, Any]:
        refs = tuple(
            MaterialReportRef(item.material_id, item.version_policy, item.version)
            for item in body.material_refs
        )
        view = report_view(title=body.title, narration=body.narration, material_refs=refs)
        return {
            "project_id": project_id,
            "markdown": render_report(view, get_runtime().service(project_id)),
            "material_refs": [asdict(item) for item in refs],
        }

    return _execute(operation)


__all__ = ["material_wiki_router", "get_runtime"]
