from __future__ import annotations

import copy
import uuid
from typing import Any

from .models import ReportAnnotation, ReportPatch, ReportPatchOperation, utc_now
from .store import ObservatoryStore


class EditorialError(RuntimeError):
    pass


class PatchConflict(EditorialError):
    pass


_COLLECTIONS = {
    "flow": "flow",
    "mechanism": "mechanisms",
    "resource": "resources",
    "claim": "claims",
    "observation": "observations",
    "interpretation": "interpretations",
    "source": "sources",
    "voice": "player_voices",
    "surface": "surfaces",
}


class EditorialService:
    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    def propose_patch(
        self,
        report_id: str,
        *,
        base_revision: int,
        author: str,
        note: str,
        operations: list[ReportPatchOperation],
    ) -> ReportPatch:
        if not self.store.get_report(report_id):
            raise EditorialError(f"report not found: {report_id}")
        patch = ReportPatch(
            id=f"patch.{uuid.uuid4().hex}",
            report_id=report_id,
            base_revision=base_revision,
            author=author.strip(),
            note=note.strip(),
            operations=operations,
        )
        if not patch.author or not patch.note:
            raise EditorialError("patch author and note are required")
        self.store.save_report_patch(patch)
        return patch

    @staticmethod
    def _find(collection: list[dict[str, Any]], target_id: str | None) -> tuple[int, dict[str, Any]]:
        for index, item in enumerate(collection):
            if isinstance(item, dict) and item.get("id") == target_id:
                return index, item
        raise EditorialError(f"patch target not found: {target_id}")

    @classmethod
    def _apply_operation(
        cls, payload: dict[str, Any], operation: ReportPatchOperation
    ) -> None:
        if operation.target_kind == "report":
            if operation.target_id and operation.target_id != payload.get("id"):
                raise EditorialError(f"report target mismatch: {operation.target_id}")
            if not operation.field:
                raise EditorialError("report patch operation requires field")
            if operation.field in {"id", "slug", "game_id", "system_id", "created_at"}:
                raise EditorialError(f"immutable report field: {operation.field}")
            if operation.op == "remove":
                payload.pop(operation.field, None)
            else:
                payload[operation.field] = copy.deepcopy(operation.value)
            if operation.field == "summary" and payload.get("summary_claim"):
                payload["summary_claim"]["statement"] = payload["summary"]
            return

        collection_name = _COLLECTIONS[operation.target_kind]
        collection = payload.setdefault(collection_name, [])
        if not isinstance(collection, list):
            raise EditorialError(f"target collection is not a list: {collection_name}")
        if operation.op == "add":
            if not isinstance(operation.value, dict) or not operation.value.get("id"):
                raise EditorialError("add requires an object value with stable id")
            if any(item.get("id") == operation.value["id"] for item in collection if isinstance(item, dict)):
                raise EditorialError(f"duplicate object id: {operation.value['id']}")
            collection.append(copy.deepcopy(operation.value))
            return

        index, target = cls._find(collection, operation.target_id)
        if operation.op == "remove" and not operation.field:
            collection.pop(index)
            return
        if not operation.field:
            raise EditorialError("replace/remove field operation requires field")
        if operation.field == "id":
            raise EditorialError("stable object ids are immutable")
        if operation.op == "remove":
            target.pop(operation.field, None)
        else:
            target[operation.field] = copy.deepcopy(operation.value)

    def apply_patch(self, patch_id: str, *, reviewer: str) -> ReportPatch:
        patch = self.store.get_report_patch(patch_id)
        if not patch:
            raise EditorialError(f"patch not found: {patch_id}")
        if patch.status != "proposed":
            raise EditorialError(f"patch is already {patch.status}")
        current_revision = self.store.current_revision(patch.report_id)
        if current_revision != patch.base_revision:
            raise PatchConflict(
                f"base revision {patch.base_revision} is stale; current revision is {current_revision}"
            )
        report = self.store.get_report(patch.report_id)
        if not report:
            raise EditorialError(f"report not found: {patch.report_id}")
        payload = report.model_dump(mode="json")
        for operation in patch.operations:
            self._apply_operation(payload, operation)
        payload["updated_at"] = utc_now()
        from .models import GameReport

        updated = GameReport.model_validate(payload)
        updated.assert_publishable()
        self.store.upsert_report(updated)
        applied_revision = self.store.current_revision(updated.id)
        if applied_revision == current_revision:
            raise EditorialError("patch produced no canonical change")
        applied = patch.model_copy(
            update={
                "status": "applied",
                "applied_at": utc_now(),
                "applied_revision": applied_revision,
                "note": f"{patch.note}\
Reviewed by {reviewer.strip()}",
            }
        )
        self.store.save_report_patch(applied)
        return applied

    def reject_patch(self, patch_id: str, *, reviewer: str, reason: str) -> ReportPatch:
        patch = self.store.get_report_patch(patch_id)
        if not patch:
            raise EditorialError(f"patch not found: {patch_id}")
        if patch.status != "proposed":
            raise EditorialError(f"patch is already {patch.status}")
        rejected = patch.model_copy(
            update={
                "status": "rejected",
                "rejected_at": utc_now(),
                "rejection_reason": f"{reason.strip()} (reviewer: {reviewer.strip()})",
            }
        )
        self.store.save_report_patch(rejected)
        return rejected

    @staticmethod
    def _object_ids(report: Any) -> set[str]:
        ids = {report.id, report.scope.id}
        for value in (
            report.sources,
            report.artifacts,
            report.runs,
            report.surfaces,
            report.claims,
            report.flow,
            report.mechanisms,
            report.resources,
            report.player_voices,
        ):
            ids.update(item.id for item in value if getattr(item, "id", None))
        ids.update(item.id for item in report.observations if getattr(item, "id", None))
        ids.update(item.id for item in report.interpretations if getattr(item, "id", None))
        for value in (report.game, report.system_concept, report.system_instance, report.resource_model):
            if value:
                ids.add(value.id)
        return ids

    def annotate(
        self,
        report_id: str,
        *,
        object_id: str,
        author: str,
        body: str,
        kind: str = "comment",
        source_ids: list[str] | None = None,
    ) -> ReportAnnotation:
        report = self.store.get_report(report_id)
        if not report:
            raise EditorialError(f"report not found: {report_id}")
        if object_id not in self._object_ids(report):
            raise EditorialError(f"annotation target not found: {object_id}")
        missing_sources = set(source_ids or []) - {item.id for item in report.sources}
        if missing_sources:
            raise EditorialError(f"annotation references missing sources: {sorted(missing_sources)}")
        annotation = ReportAnnotation(
            id=f"annotation.{uuid.uuid4().hex}",
            report_id=report_id,
            object_id=object_id,
            author=author.strip(),
            body=body.strip(),
            kind=kind,
            source_ids=source_ids or [],
        )
        if not annotation.author or not annotation.body:
            raise EditorialError("annotation author and body are required")
        self.store.save_report_annotation(annotation)
        return annotation

    def resolve_annotation(self, annotation_id: str, *, reviewer: str) -> ReportAnnotation:
        annotation = self.store.get_report_annotation(annotation_id)
        if not annotation:
            raise EditorialError(f"annotation not found: {annotation_id}")
        resolved = annotation.model_copy(
            update={"status": "resolved", "resolved_at": utc_now(), "resolved_by": reviewer}
        )
        self.store.save_report_annotation(resolved)
        return resolved