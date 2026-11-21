# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="材料 Wiki 投影协调器：本体成功后以持久 outbox 幂等更新 ID 索引、事件和语义检索"
# [OMNI] why="投影可能暂时失败，但不能反过来制造假本体或吞掉失败；持久回执让同一幂等键重放时只补失败项目"
# [OMNI] tags=material-wiki,projection,outbox,reviewstage,semantic-fs,event
# [OMNI] material_id="material:core.material_wiki.projections.py"
"""Durable, replayable projections derived from canonical Material Wiki bodies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Protocol

from omnicompany.packages.services._core.registry.material_index import (
    MaterialIdEntry,
    get_material_id_index,
)

from .adapters.markdown import MarkdownWikiAdapter, WikiDocument, WikiOperation


@dataclass(frozen=True, slots=True)
class ProjectionOutcome:
    name: str
    status: str
    reference: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class ProjectionSummary:
    mutation_id: str
    outcomes: tuple[ProjectionOutcome, ...]

    @property
    def pending(self) -> bool:
        return any(item.status != "succeeded" for item in self.outcomes)


@dataclass(frozen=True, slots=True)
class ProjectionRebuildSummary:
    documents: int
    versions: int
    pending: int
    results: tuple[ProjectionSummary, ...]


@dataclass(frozen=True, slots=True)
class ProjectionContext:
    mutation_id: str
    document: WikiDocument
    operation: WikiOperation
    canonical_path: Path

    @property
    def version(self):
        return self.document.version(self.operation.version)


class Projector(Protocol):
    name: str

    def project(self, context: ProjectionContext) -> str: ...


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = handle.name
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def _source_mapping(source: Any) -> dict[str, Any]:
    result = {"kind": source.kind, "ref": source.ref}
    if source.note:
        result["note"] = source.note
    return result


def _relation_mapping(relation: Any) -> dict[str, str]:
    return {"material_id": relation.material_id, "version": relation.version}


def event_payload(context: ProjectionContext) -> dict[str, Any]:
    version = context.version
    relations: dict[str, Any] = {}
    if version.relations.parent_version:
        relations["parent_version"] = _relation_mapping(version.relations.parent_version)
    for key in ("supersedes", "derived_from", "related"):
        values = getattr(version.relations, key)
        if values:
            relations[key] = [_relation_mapping(item) for item in values]
    return {
        "id": context.document.material_id,
        "material_id": context.document.material_id,
        "project_id": context.document.project_id,
        "profile_id": context.document.profile_id,
        "title": context.document.title,
        "canonical_ref": context.document.canonical_ref,
        "path": context.canonical_path.as_posix(),
        "placement_path": list(context.document.placement_path),
        "version": context.operation.version,
        "current_version": context.operation.current_version,
        "status": context.operation.status,
        "sources": [_source_mapping(item) for item in context.operation.sources],
        "relations": relations,
        "author_session": context.operation.author_session,
        "mutation_id": context.mutation_id,
    }


class MaterialIdProjector:
    name = "material_id_index"

    def __init__(self, *, registry_dir: Path | None = None) -> None:
        self.registry_dir = registry_dir

    def project(self, context: ProjectionContext) -> str:
        get_material_id_index(self.registry_dir).upsert(
            MaterialIdEntry(
                material_id=context.document.material_id,
                file_path=context.canonical_path.as_posix(),
                kind="material_wiki",
                origin="canonical",
                domain=context.document.project_id,
                summary=context.document.title,
                why="canonical Material Wiki body",
                tags=("material-wiki", *context.document.placement_path),
            )
        )
        return context.document.material_id


class FactoryEventProjector:
    name = "factory_event"

    def project(self, context: ProjectionContext) -> str:
        from omnicompany.packages.services._core.omnicompany.material_events import (
            publish_material_event,
        )

        event_id = f"mw_{hashlib.sha256(context.mutation_id.encode('utf-8')).hexdigest()[:24]}"
        result = publish_material_event(
            "omni.material-wiki-entry",
            event_payload(context),
            source="material_wiki.service",
            trace_id=context.operation.author_session,
            tags=(
                "material-wiki",
                f"project.{context.document.project_id}",
                f"status.{context.operation.status}",
            ),
            event_id=event_id,
        )
        if not result:
            raise RuntimeError("FactoryEvent 发布失败")
        return result


class FileFactoryEventProjector:
    """Isolated-facility event projection using the same payload without global DB writes."""

    name = "factory_event"

    def __init__(self, root: Path) -> None:
        self.root = root

    def project(self, context: ProjectionContext) -> str:
        event_id = f"mw_{hashlib.sha256(context.mutation_id.encode('utf-8')).hexdigest()[:24]}"
        path = self.root / f"{event_id}.json"
        _atomic_json(
            path,
            {
                "id": event_id,
                "event_type": "omni.material-wiki-entry",
                "source": "material_wiki.service",
                "trace_id": context.operation.author_session,
                "payload": event_payload(context),
            },
        )
        return event_id


class SemanticFsProjector:
    name = "semantic_fs"

    def __init__(self, *, root: Path | None = None) -> None:
        self.root = root

    def project(self, context: ProjectionContext) -> str:
        from omnicompany.packages.services._core.semantic_fs.index import (
            upsert_text_projection,
        )

        result = upsert_text_projection(
            entity_id=context.document.material_id,
            file_path=context.canonical_path.as_posix(),
            summary=context.document.title,
            text=context.version.body,
            semantic_tags=["kind.source", "type.material-wiki"],
            root=self.root,
        )
        return str(result["db"])


class ReviewStageProjector:
    name = "reviewstage"

    def __init__(self, *, store_factory: Callable[[], Any] | None = None) -> None:
        self.store_factory = store_factory or self._default_store

    @staticmethod
    def _default_store() -> Any:
        from omnicompany.packages.services.boss_sight.reviewstage.store import MaterialStore

        return MaterialStore()

    def project(self, context: ProjectionContext) -> str:
        store = self.store_factory()
        from omnicompany.packages.services.boss_sight.reviewstage.material_types import (
            UNFILED_PROJECT,
            known_review_projects,
        )

        existing = [
            item
            for item in store.list(
                include_archived=True,
                include_internal=True,
            )
            if (item.extra or {}).get("canonical_material_id") == context.document.material_id
            and (item.extra or {}).get("canonical_version") == context.operation.version
        ]
        if existing:
            store.patch_extra(
                existing[0].id,
                {
                    "canonical_status": context.operation.status,
                    "canonical_current_version": context.operation.current_version,
                    "canonical_mutation_id": context.mutation_id,
                },
                by="material_wiki_projection",
            )
            return existing[0].id
        match = re.fullmatch(r"v([1-9][0-9]*)", context.operation.version)
        version_number = int(match.group(1)) if match else len(context.document.versions)
        review_project = (
            context.document.project_id
            if context.document.project_id in known_review_projects()
            else UNFILED_PROJECT
        )
        material = store.create(
            kind="markdown",
            tier="processual",
            title=f"{context.document.title} · {context.operation.version}",
            file_relpath=context.canonical_path.as_posix(),
            annotations_allowed=True,
            extra={
                "projection_role": "material_wiki_review",
                # Wiki 本体的 ReviewStage 条目只是供显式审阅/技术回溯使用的
                # 派生投影，不是要求作者逐篇处理的审阅任务。
                "reviewstage_visibility": "internal",
                "canonical_material_id": context.document.material_id,
                "canonical_project_id": context.document.project_id,
                "canonical_ref": context.document.canonical_ref,
                "canonical_version": context.operation.version,
                "canonical_status": context.operation.status,
                "canonical_current_version": context.operation.current_version,
                "canonical_mutation_id": context.mutation_id,
                "canonical_body_only": True,
            },
            project=review_project,
            track="/".join(context.document.placement_path),
            version=version_number,
            version_family=context.document.material_id,
        )
        return material.id


class ProjectionCoordinator:
    def __init__(
        self,
        state_root: str | Path,
        projectors: tuple[Projector, ...],
    ) -> None:
        self.state_root = Path(state_root).resolve()
        self.projectors = projectors

    def _state_path(self, mutation_id: str) -> Path:
        digest = hashlib.sha256(mutation_id.encode("utf-8")).hexdigest()
        return self.state_root / f"{digest}.json"

    def process(
        self,
        adapter: MarkdownWikiAdapter,
        document: WikiDocument,
        operation: WikiOperation,
        *,
        force: bool = False,
    ) -> ProjectionSummary:
        mutation_id = f"{document.material_id}:{operation.key}"
        state_path = self._state_path(mutation_id)
        state: dict[str, Any] = {
            "schema_version": 1,
            "mutation_id": mutation_id,
            "material_id": document.material_id,
            "operation": operation.operation,
            "version": operation.version,
            "canonical_ref": document.canonical_ref,
            "projectors": {},
        }
        if state_path.is_file():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
            except (OSError, json.JSONDecodeError):
                pass
        _atomic_json(state_path, state)
        canonical_path, _ = adapter.locate(document.material_id)
        context = ProjectionContext(
            mutation_id=mutation_id,
            document=document,
            operation=operation,
            canonical_path=canonical_path,
        )
        outcomes: list[ProjectionOutcome] = []
        projector_state = state.setdefault("projectors", {})
        for projector in self.projectors:
            prior = projector_state.get(projector.name) or {}
            if prior.get("status") == "succeeded" and not force:
                outcomes.append(
                    ProjectionOutcome(
                        projector.name,
                        "succeeded",
                        reference=str(prior.get("reference") or ""),
                    )
                )
                continue
            try:
                reference = projector.project(context)
                outcome = ProjectionOutcome(projector.name, "succeeded", reference=reference)
            except Exception as exc:  # noqa: BLE001 - failures are persisted for replay
                outcome = ProjectionOutcome(projector.name, "pending", error=str(exc))
            projector_state[projector.name] = asdict(outcome)
            _atomic_json(state_path, state)
            outcomes.append(outcome)
        return ProjectionSummary(mutation_id, tuple(outcomes))

    def rebuild(
        self,
        adapter: MarkdownWikiAdapter,
        documents: tuple[WikiDocument, ...],
    ) -> ProjectionRebuildSummary:
        """Recreate every derived view from canonical version bodies.

        Rebuild uses one deterministic snapshot mutation per canonical version.  It does
        not alter the Wiki body or its operation journal, and `force=True` deliberately
        ignores a prior successful outbox row so deleted projections can be restored.
        """
        results: list[ProjectionSummary] = []
        for document in documents:
            for version in document.versions:
                operation = WikiOperation(
                    key=f"rebuild:{document.material_id}:{version.version}",
                    operation="rebuild",
                    version=version.version,
                    status=version.status,
                    current_version=document.current_version,
                    created_at=version.created_at,
                    sources=version.sources,
                    author_session=version.author_session,
                )
                results.append(self.process(adapter, document, operation, force=True))
        return ProjectionRebuildSummary(
            documents=len(documents),
            versions=len(results),
            pending=sum(result.pending for result in results),
            results=tuple(results),
        )


def build_default_projection_coordinator(
    *,
    state_root: str | Path,
    semantic_root: Path | None = None,
    registry_dir: Path | None = None,
    isolated_events_root: Path | None = None,
) -> ProjectionCoordinator:
    return ProjectionCoordinator(
        state_root,
        (
            MaterialIdProjector(registry_dir=registry_dir),
            (
                FileFactoryEventProjector(isolated_events_root)
                if isolated_events_root is not None
                else FactoryEventProjector()
            ),
            SemanticFsProjector(root=semantic_root),
        ),
    )
