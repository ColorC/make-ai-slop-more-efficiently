# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="材料 Wiki 唯一 mutation 服务；统一执行 create/revise/derive/supersede/reject/restore/link，并返回稳定本体引用"
# [OMNI] why="Agent、CLI、HTTP 和审阅投影必须共享先写本体的同一入口，避免各自产生一份可漂移的当前材料"
# [OMNI] tags=material-wiki,service,mutation,idempotency,versioning
# [OMNI] material_id="material:core.material_wiki.service.py"
"""Canonical-first Material Wiki mutation service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import re
from typing import Callable, Iterable

from omnicompany.packages.services._core.identity import current_session_meta

from .adapters.markdown import (
    AdapterMutationResult,
    MarkdownWikiAdapter,
    MaterialEntryNotFoundError,
    WikiDocument,
    WikiVersion,
)
from .schema import (
    MaterialRelations,
    MaterialSourceRef,
    MaterialVersionRef,
    MaterialWikiContractError,
    MaterialWikiEntry,
    MaterialWikiProfile,
)
from .projections import ProjectionCoordinator, ProjectionOutcome


_VERSION_NUMBER = re.compile(r"^v(?P<number>[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class MaterialMutationReceipt:
    operation: str
    material_id: str
    version: str
    current_version: str | None
    status: str
    canonical_ref: str
    idempotent_replay: bool
    dry_run: bool
    projections: tuple[ProjectionOutcome, ...] = ()
    projection_pending: bool = False


def _default_session() -> str:
    return str(current_session_meta().get("trace_id") or "unknown-session")


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_version(current: str) -> str:
    matched = _VERSION_NUMBER.fullmatch(current)
    if not matched:
        raise ValueError(f"无法从非数字版本 {current!r} 自动生成下一版；请显式提供 version")
    return f"v{int(matched.group('number')) + 1}"


def _required_sources(sources: Iterable[MaterialSourceRef]) -> tuple[MaterialSourceRef, ...]:
    values = tuple(sources)
    if not values:
        raise ValueError("每次材料 mutation 都必须至少提供一个 source")
    return values


def _required_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("idempotency_key 不能为空")
    return normalized


def _replace_version(document: WikiDocument, replacement: WikiVersion) -> WikiDocument:
    versions = tuple(
        replacement if item.version == replacement.version else item
        for item in document.versions
    )
    return replace(document, versions=versions)


class MaterialWikiService:
    """Validate project profile rules before any canonical Markdown mutation."""

    def __init__(
        self,
        profile: MaterialWikiProfile,
        adapter: MarkdownWikiAdapter,
        *,
        session_provider: Callable[[], str] = _default_session,
        clock: Callable[[], str] = _default_clock,
        projection_coordinator: ProjectionCoordinator | None = None,
    ) -> None:
        self.profile = profile
        self.adapter = adapter
        self.session_provider = session_provider
        self.clock = clock
        self.projection_coordinator = projection_coordinator

    def _deactivate_current(self, document: WikiDocument) -> WikiDocument:
        if not document.current_version or "superseded" not in self.profile.status_ids:
            return document
        current = document.current
        if current is None:
            return document
        superseded = replace(
            current,
            status="superseded",
            prior_status=current.status,
        )
        return _replace_version(document, superseded)

    def _receipt(self, result: AdapterMutationResult) -> MaterialMutationReceipt:
        operation = result.operation
        outcomes: tuple[ProjectionOutcome, ...] = ()
        if self.projection_coordinator is not None and not result.dry_run:
            try:
                outcomes = self.projection_coordinator.process(
                    self.adapter,
                    result.document,
                    operation,
                ).outcomes
            except Exception as exc:  # noqa: BLE001 - body is committed; replay repairs projection
                outcomes = (
                    ProjectionOutcome(
                        name="projection_outbox",
                        status="pending",
                        error=str(exc),
                    ),
                )
        return MaterialMutationReceipt(
            operation=operation.operation,
            material_id=result.document.material_id,
            version=operation.version,
            current_version=operation.current_version,
            status=operation.status,
            canonical_ref=result.document.canonical_ref,
            idempotent_replay=result.idempotent_replay,
            dry_run=result.dry_run,
            projections=outcomes,
            projection_pending=any(item.status != "succeeded" for item in outcomes),
        )

    def _validate_version(self, document: WikiDocument, version: WikiVersion) -> None:
        entry = MaterialWikiEntry(
            material_id=document.material_id,
            project_id=document.project_id,
            profile_id=document.profile_id,
            title=document.title,
            canonical_ref=document.canonical_ref,
            placement_path=document.placement_path,
            version=version.version,
            status=version.status,
            sources=version.sources,
            relations=version.relations,
            author_session=version.author_session,
        )
        entry.validate_against(self.profile)

    def _material_id(self, title: str, idempotency_key: str) -> str:
        if self.profile.project_id == "vilo":
            slug = re.sub(r"[^A-Za-z\u3400-\u9fff0-9]+", "", title)[:48] or "未命名"
            candidate = f"Vilo.材料.{slug}"
            try:
                self.adapter.load(candidate)
            except MaterialEntryNotFoundError:
                return candidate
            numeric = int(hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(), 16) % 10_000_000_000
            return f"{candidate}.{numeric:010d}"
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "entry"
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:10]
        return f"{self.profile.project_id}.material.{slug[:48]}-{digest}"

    def create(
        self,
        *,
        title: str,
        placement_path: Iterable[str],
        body: str,
        status: str,
        sources: Iterable[MaterialSourceRef],
        idempotency_key: str,
        material_id: str | None = None,
        version: str = "v1",
        relations: MaterialRelations | None = None,
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        idempotency_key = _required_idempotency_key(idempotency_key)
        placement = self.profile.validate_path(tuple(placement_path))
        material_id = material_id or self._material_id(title, idempotency_key)
        canonical_ref = self.adapter.canonical_ref(material_id, placement)
        wiki_version = WikiVersion(
            version=version,
            status=status,
            body=body,
            sources=_required_sources(sources),
            relations=relations or MaterialRelations(),
            author_session=self.session_provider(),
            created_at=self.clock(),
        )
        status_definition = next(
            (item for item in self.profile.statuses if item.id == status),
            None,
        )
        current_version = version if status_definition and status_definition.current else None
        document = WikiDocument(
            material_id=material_id,
            project_id=self.profile.project_id,
            profile_id=self.profile.profile_id,
            title=title,
            canonical_ref=canonical_ref,
            placement_path=placement,
            head_version=version,
            current_version=current_version,
            versions=(wiki_version,),
        )
        self._validate_version(document, wiki_version)
        return self._receipt(
            self.adapter.create(
                document,
                idempotency_key=idempotency_key,
                dry_run=dry_run,
            )
        )

    def revise(
        self,
        material_id: str,
        *,
        body: str,
        status: str,
        sources: Iterable[MaterialSourceRef],
        expected_version: str,
        idempotency_key: str,
        version: str | None = None,
        relations: MaterialRelations | None = None,
        operation_name: str = "revise",
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        idempotency_key = _required_idempotency_key(idempotency_key)
        source_tuple = _required_sources(sources)
        author_session = self.session_provider()

        def mutation(document: WikiDocument) -> tuple[WikiDocument, str, str]:
            new_version_id = version or _next_version(document.head_version)
            if any(item.version == new_version_id for item in document.versions):
                raise ValueError(f"版本 {new_version_id} 已存在")
            relation_value = relations or MaterialRelations(
                parent_version=MaterialVersionRef(material_id, document.head_version)
            )
            new_version = WikiVersion(
                version=new_version_id,
                status=status,
                body=body,
                sources=source_tuple,
                relations=relation_value,
                author_session=author_session,
                created_at=self.clock(),
            )
            status_definition = next(
                (item for item in self.profile.statuses if item.id == status),
                None,
            )
            current_version = (
                new_version_id if status_definition and status_definition.current else document.current_version
            )
            base_document = (
                self._deactivate_current(document)
                if status_definition and status_definition.current
                else document
            )
            updated = replace(
                base_document,
                head_version=new_version_id,
                current_version=current_version,
                versions=(*base_document.versions, new_version),
            )
            self._validate_version(updated, new_version)
            return updated, new_version_id, status

        return self._receipt(
            self.adapter.mutate(
                material_id,
                operation_name=operation_name,
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                operation_sources=source_tuple,
                author_session=author_session,
                mutation=mutation,
                dry_run=dry_run,
            )
        )

    def retitle(
        self,
        material_id: str,
        *,
        title: str,
        sources: Iterable[MaterialSourceRef],
        expected_version: str,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        """只改材料显示标题(不动任何版本正文)——如把生成期英文 slug 标题扶正为中文名。"""
        idempotency_key = _required_idempotency_key(idempotency_key)
        source_tuple = _required_sources(sources)
        author_session = self.session_provider()
        clean = title.strip()
        if not clean:
            raise ValueError("标题不能为空")

        def mutation(document: WikiDocument) -> tuple[WikiDocument, str, str]:
            if document.title == clean:
                raise ValueError(f"标题已是 {clean}")
            return replace(document, title=clean), document.head_version, "retitle"

        return self._receipt(
            self.adapter.mutate(
                material_id,
                operation_name="retitle",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                operation_sources=source_tuple,
                author_session=author_session,
                mutation=mutation,
                dry_run=dry_run,
            )
        )

    def derive(
        self,
        source_material_id: str,
        *,
        source_version: str,
        title: str,
        placement_path: Iterable[str],
        body: str,
        status: str,
        sources: Iterable[MaterialSourceRef],
        idempotency_key: str,
        material_id: str | None = None,
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        idempotency_key = _required_idempotency_key(idempotency_key)
        source_document = self.adapter.load(source_material_id)
        source_document.version(source_version)
        return self.create(
            title=title,
            placement_path=placement_path,
            body=body,
            status=status,
            sources=sources,
            idempotency_key=idempotency_key,
            material_id=material_id,
            relations=MaterialRelations(
                derived_from=(MaterialVersionRef(source_material_id, source_version),)
            ),
            dry_run=dry_run,
        )

    def supersede(
        self,
        material_id: str,
        *,
        body: str,
        status: str,
        sources: Iterable[MaterialSourceRef],
        expected_version: str,
        idempotency_key: str,
        version: str | None = None,
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        return self.revise(
            material_id,
            body=body,
            status=status,
            sources=sources,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            version=version,
            relations=MaterialRelations(
                parent_version=MaterialVersionRef(material_id, expected_version),
                supersedes=(MaterialVersionRef(material_id, expected_version),),
            ),
            operation_name="supersede",
            dry_run=dry_run,
        )

    def reject(
        self,
        material_id: str,
        *,
        target_version: str,
        sources: Iterable[MaterialSourceRef],
        expected_version: str,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        if "rejected" not in self.profile.status_ids:
            raise ValueError(f"{self.profile.profile_id} 未定义 rejected 状态")

        idempotency_key = _required_idempotency_key(idempotency_key)
        source_tuple = _required_sources(sources)
        author_session = self.session_provider()

        def mutation(document: WikiDocument) -> tuple[WikiDocument, str, str]:
            target = document.version(target_version)
            rejected = replace(target, status="rejected", prior_status=target.status)
            updated = _replace_version(document, rejected)
            if document.current_version == target_version:
                current_status_ids = {
                    item.id for item in self.profile.statuses if item.current
                }
                candidates = [
                    item
                    for item in document.versions
                    if item.version != target_version
                    and (
                        item.status in current_status_ids
                        or (
                            item.status == "superseded"
                            and item.prior_status in current_status_ids
                        )
                    )
                ]
                fallback = candidates[-1] if candidates else None
                if fallback and fallback.status == "superseded" and fallback.prior_status:
                    fallback = replace(
                        fallback,
                        status=fallback.prior_status,
                        prior_status=None,
                    )
                    updated = _replace_version(updated, fallback)
                fallback_status = next(
                    (
                        item
                        for item in self.profile.statuses
                        if fallback and item.id == fallback.status
                    ),
                    None,
                )
                updated = replace(
                    updated,
                    current_version=(
                        fallback.version
                        if fallback and fallback_status and fallback_status.current
                        else None
                    ),
                )
            self._validate_version(updated, rejected)
            return updated, target_version, "rejected"

        return self._receipt(
            self.adapter.mutate(
                material_id,
                operation_name="reject",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                operation_sources=source_tuple,
                author_session=author_session,
                mutation=mutation,
                dry_run=dry_run,
            )
        )

    def restore(
        self,
        material_id: str,
        *,
        target_version: str,
        status: str,
        sources: Iterable[MaterialSourceRef],
        expected_version: str,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        status_definition = next(
            (item for item in self.profile.statuses if item.id == status),
            None,
        )
        if status_definition is None or not status_definition.current:
            raise ValueError(f"restore 目标状态必须是 current，实际为 {status}")

        idempotency_key = _required_idempotency_key(idempotency_key)
        source_tuple = _required_sources(sources)
        author_session = self.session_provider()

        def mutation(document: WikiDocument) -> tuple[WikiDocument, str, str]:
            target = replace(
                document.version(target_version),
                status=status,
                prior_status=None,
            )
            updated = self._deactivate_current(document)
            updated = _replace_version(updated, target)
            updated = replace(updated, current_version=target_version)
            self._validate_version(updated, target)
            return updated, target_version, status

        return self._receipt(
            self.adapter.mutate(
                material_id,
                operation_name="restore",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                operation_sources=source_tuple,
                author_session=author_session,
                mutation=mutation,
                dry_run=dry_run,
            )
        )

    def link(
        self,
        material_id: str,
        *,
        target_version: str,
        relation: str,
        related: MaterialVersionRef,
        sources: Iterable[MaterialSourceRef],
        expected_version: str,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> MaterialMutationReceipt:
        if relation not in {"supersedes", "derived_from", "related"}:
            raise ValueError(f"不支持关系 {relation}")

        idempotency_key = _required_idempotency_key(idempotency_key)
        source_tuple = _required_sources(sources)
        author_session = self.session_provider()

        def mutation(document: WikiDocument) -> tuple[WikiDocument, str, str]:
            target = document.version(target_version)
            values = getattr(target.relations, relation)
            if related not in values:
                relations = replace(target.relations, **{relation: (*values, related)})
                target = replace(target, relations=relations)
            updated = _replace_version(document, target)
            self._validate_version(updated, target)
            return updated, target_version, target.status

        return self._receipt(
            self.adapter.mutate(
                material_id,
                operation_name="link",
                idempotency_key=idempotency_key,
                expected_version=expected_version,
                operation_sources=source_tuple,
                author_session=author_session,
                mutation=mutation,
                dry_run=dry_run,
            )
        )

    def get(self, material_id: str) -> WikiDocument:
        return self.adapter.load(material_id)

    def require_version(self, material_id: str, version: str) -> WikiVersion:
        try:
            return self.adapter.load(material_id).version(version)
        except MaterialEntryNotFoundError:
            raise
