"""Structural validation, stable manifests, and View resolution for Material Core."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Literal, TypeAlias

MATERIAL_CORE_VERSION = "0.2.0"
SUPPORTED_MATERIAL_CORE_VERSIONS = frozenset({"0.1.0", MATERIAL_CORE_VERSION})

JsonObject: TypeAlias = dict[str, Any]
ViewMode: TypeAlias = Literal["source", "expanded"]
_ISO_8601_WITH_TIMEZONE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True, slots=True)
class MaterialGraphIssue:
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    issues: tuple[MaterialGraphIssue, ...]


class MaterialGraphValidationError(TypeError):
    """Raised when an API that needs a valid graph receives an invalid graph."""


def _record(value: object) -> bool:
    return isinstance(value, Mapping)


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _array_of_non_empty(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_non_empty(item) for item in value)


def _source_ref(value: object) -> bool:
    return _record(value) and _non_empty(value.get("kind")) and _non_empty(value.get("ref"))


def _revision_ref(value: object) -> bool:
    return (
        _record(value)
        and _non_empty(value.get("provider"))
        and _non_empty(value.get("object"))
        and _non_empty(value.get("revision"))
    )


def _version_ref(value: object) -> bool:
    return _record(value) and _non_empty(value.get("material_id")) and _revision_ref(value.get("revision_ref"))


def _actor_ref(value: object) -> bool:
    return _record(value) and _non_empty(value.get("kind")) and _non_empty(value.get("ref"))


def _authority_scope(value: object) -> bool:
    return (
        _record(value)
        and _non_empty(value.get("object"))
        and _non_empty(value.get("purpose"))
        and _non_empty(value.get("scope"))
    )


def _artifact_ref(value: object) -> bool:
    return _record(value) and _non_empty(value.get("kind")) and _non_empty(value.get("ref")) and _revision_ref(value.get("revision_ref"))


def _iso8601(value: object) -> bool:
    if not isinstance(value, str) or not _ISO_8601_WITH_TIMEZONE.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate_material_graph(input: object) -> ValidationResult:
    """Validate a v0.1/v0.2 Material Graph without making governance judgments."""
    issues: list[MaterialGraphIssue] = []

    def issue(path: str, message: str) -> None:
        issues.append(MaterialGraphIssue(path, message))

    if not _record(input):
        return ValidationResult(False, (MaterialGraphIssue("$", "graph must be an object"),))
    version = input.get("contract_version")
    if version not in SUPPORTED_MATERIAL_CORE_VERSIONS:
        issue("contract_version", "expected one of 0.1.0, 0.2.0")
    if not _non_empty(input.get("graph_id")):
        issue("graph_id", "must be a non-empty string")
    if not _iso8601(input.get("generated_at")):
        issue("generated_at", "must be an ISO-8601 date-time string")
    materials = input.get("materials")
    if not isinstance(materials, list):
        issue("materials", "must be an array")
    if issues or not isinstance(materials, list):
        return ValidationResult(False, tuple(issues))

    ids: set[str] = set()
    for index, material in enumerate(materials):
        path = f"materials[{index}]"
        if not _record(material):
            issue(path, "must be an object")
            continue
        material_id = material.get("id")
        if not _non_empty(material_id):
            issue(f"{path}.id", "must be a non-empty string")
        elif material_id in ids:
            issue(f"{path}.id", f"duplicate material id {material_id}")
        else:
            ids.add(material_id)
        if not _non_empty(material.get("title")):
            issue(f"{path}.title", "must be a non-empty string")
        labels = material.get("labels")
        if not isinstance(labels, list) or any(not _non_empty(label) for label in labels):
            issue(f"{path}.labels", "must be an array of non-empty strings")
        if not _revision_ref(material.get("revision_ref")):
            issue(f"{path}.revision_ref", "must contain provider, object and revision")
        _validate_representations(material.get("representations"), f"{path}.representations", issue)
        _validate_relationship(material.get("relationship"), f"{path}.relationship", issue)
        _validate_view(material.get("view"), f"{path}.view", issue)
        if version == MATERIAL_CORE_VERSION:
            _validate_v02_fields(material, path, issue)

    for index, material in enumerate(materials):
        if not _record(material):
            continue
        relationship = material.get("relationship")
        if _record(relationship):
            if _non_empty(relationship.get("from")) and relationship["from"] not in ids:
                issue(f"materials[{index}].relationship.from", "target material is absent")
            if _non_empty(relationship.get("to")) and relationship["to"] not in ids:
                issue(f"materials[{index}].relationship.to", "target material is absent")
        view = material.get("view")
        if _record(view) and isinstance(view.get("members"), list):
            for member_index, member in enumerate(view["members"]):
                if _record(member) and _non_empty(member.get("material_id")) and member["material_id"] not in ids:
                    issue(f"materials[{index}].view.members[{member_index}].material_id", "member material is absent")
    active_view_id = input.get("active_view_id")
    if _non_empty(active_view_id) and active_view_id not in ids:
        issue("active_view_id", "active view is absent")
    return ValidationResult(not issues, tuple(issues))


def _validate_representations(value: object, path: str, issue: Any) -> None:
    if not isinstance(value, list) or not value:
        issue(path, "must contain at least one representation")
        return
    ids: set[str] = set()
    for index, representation in enumerate(value):
        rep_path = f"{path}[{index}]"
        if not _record(representation):
            issue(rep_path, "must be an object")
            continue
        rep_id = representation.get("id")
        if not _non_empty(rep_id):
            issue(f"{rep_path}.id", "must be a non-empty string")
        elif rep_id in ids:
            issue(f"{rep_path}.id", "must be unique within material")
        else:
            ids.add(rep_id)
        if not _non_empty(representation.get("media_type")):
            issue(f"{rep_path}.media_type", "must be a non-empty string")
        if not _non_empty(representation.get("role")):
            issue(f"{rep_path}.role", "must be a non-empty string")
        if not _source_ref(representation.get("source_ref")):
            issue(f"{rep_path}.source_ref", "must contain kind and ref")


def _validate_relationship(value: object, path: str, issue: Any) -> None:
    if value is not None and (
        not _record(value)
        or not _non_empty(value.get("from"))
        or not _non_empty(value.get("predicate"))
        or not _non_empty(value.get("to"))
    ):
        issue(path, "must contain from, predicate and to")


def _validate_view(value: object, path: str, issue: Any) -> None:
    if value is None:
        return
    if not _record(value) or not isinstance(value.get("members"), list):
        issue(path, "must contain members array")
        return
    for index, member in enumerate(value["members"]):
        if not _record(member) or not _non_empty(member.get("material_id")):
            issue(f"{path}.members[{index}]", "must contain material_id")


def _validate_v02_fields(material: Mapping[str, Any], path: str, issue: Any) -> None:
    if "formal_domain" in material:
        _validate_formal_domain(material["formal_domain"], f"{path}.formal_domain", issue)
    if "formalization" in material:
        _validate_formalization(material["formalization"], f"{path}.formalization", issue)


def _validate_formal_domain(value: object, path: str, issue: Any) -> None:
    if not _record(value):
        issue(path, "must be an object")
        return
    if not _source_ref(value.get("product_ref")):
        issue(f"{path}.product_ref", "must contain kind and ref")
    if not _non_empty(value.get("semantic_scope")):
        issue(f"{path}.semantic_scope", "must be a non-empty string")
    if not _array_of_non_empty(value.get("formal_artifact_kinds")):
        issue(f"{path}.formal_artifact_kinds", "must be a non-empty array of strings")
    scopes = value.get("authority_scopes")
    if not isinstance(scopes, list) or not scopes:
        issue(f"{path}.authority_scopes", "must be a non-empty array")
    else:
        for index, scope in enumerate(scopes):
            if not _authority_scope(scope):
                issue(f"{path}.authority_scopes[{index}]", "must contain object, purpose and scope")
    if not _version_ref(value.get("rule_set_ref")):
        issue(f"{path}.rule_set_ref", "must contain material_id and revision_ref")
    responsibility = value.get("responsibility_model")
    if not _record(responsibility) or not _array_of_non_empty(responsibility.get("required_roles")):
        issue(f"{path}.responsibility_model.required_roles", "must be a non-empty array of strings")
    index_policy = value.get("index_policy")
    if not _record(index_policy) or not _non_empty(index_policy.get("default_visibility")):
        issue(f"{path}.index_policy.default_visibility", "must be a non-empty string")
    if not _record(index_policy) or not _non_empty(index_policy.get("history_visibility")):
        issue(f"{path}.index_policy.history_visibility", "must be a non-empty string")
    lifecycle = value.get("lifecycle")
    if not _record(lifecycle) or not _array_of_non_empty(lifecycle.get("operations")):
        issue(f"{path}.lifecycle.operations", "must be a non-empty array of strings")
    if not _source_ref(value.get("adapter_ref")):
        issue(f"{path}.adapter_ref", "must contain kind and ref")
    if "extensions" in value and not _record(value["extensions"]):
        issue(f"{path}.extensions", "must be an object")


def _validate_formalization(value: object, path: str, issue: Any) -> None:
    if not _record(value):
        issue(path, "must be an object")
        return
    if value.get("operation") not in {"enter", "revise", "supersede", "withdraw"}:
        issue(f"{path}.operation", "must be enter, revise, supersede or withdraw")
    if value.get("outcome") not in {"succeeded", "rejected"}:
        issue(f"{path}.outcome", "must be succeeded or rejected")
    for name in ("domain_ref", "input_material"):
        if not _version_ref(value.get(name)):
            issue(f"{path}.{name}", "must contain material_id and revision_ref")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        issue(f"{path}.artifacts", "must be a non-empty array")
    else:
        for index, artifact in enumerate(artifacts):
            if not _artifact_ref(artifact):
                issue(f"{path}.artifacts[{index}]", "must contain kind, ref and revision_ref")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        issue(f"{path}.sources", "must be a non-empty array")
    else:
        for index, source in enumerate(sources):
            if not _source_ref(source):
                issue(f"{path}.sources[{index}]", "must contain kind and ref")
    responsibility = value.get("responsibility")
    if not _record(responsibility):
        issue(f"{path}.responsibility", "must be an object")
    else:
        for role in ("execution", "content", "maintenance"):
            if not _actor_ref(responsibility.get(role)):
                issue(f"{path}.responsibility.{role}", "must contain kind and ref")
    if "base_revision_ref" in value and not _revision_ref(value["base_revision_ref"]):
        issue(f"{path}.base_revision_ref", "must contain provider, object and revision")
    rule_results = value.get("rule_results")
    if not isinstance(rule_results, list) or not rule_results:
        issue(f"{path}.rule_results", "must be a non-empty array")
    else:
        for index, result in enumerate(rule_results):
            result_path = f"{path}.rule_results[{index}]"
            if not _record(result) or not _non_empty(result.get("rule_id")):
                issue(f"{result_path}.rule_id", "must be a non-empty string")
            if not _record(result) or result.get("status") not in {"passed", "failed", "waived"}:
                issue(f"{result_path}.status", "must be passed, failed or waived")
            evidence = result.get("evidence") if _record(result) else None
            if not isinstance(evidence, list) or not evidence:
                issue(f"{result_path}.evidence", "must be a non-empty array")
            elif any(not _source_ref(item) for item in evidence):
                issue(f"{result_path}.evidence", "must contain kind and ref")
            if value.get("outcome") == "succeeded" and _record(result) and result.get("status") == "failed":
                issue(f"{result_path}.status", "succeeded formalization cannot contain failed rule results")
    effect = value.get("authority_effect")
    if not _record(effect) or effect.get("action") not in {"establish", "replace", "withdraw", "none"}:
        issue(f"{path}.authority_effect.action", "must be establish, replace, withdraw or none")
    scopes = effect.get("scopes") if _record(effect) else None
    if not isinstance(scopes, list) or not scopes:
        issue(f"{path}.authority_effect.scopes", "must be a non-empty array")
    elif any(not _authority_scope(scope) for scope in scopes):
        issue(f"{path}.authority_effect.scopes", "must contain object, purpose and scope")
    replaces = effect.get("replaces") if _record(effect) else None
    if replaces is not None and (not isinstance(replaces, list) or any(not _version_ref(item) for item in replaces)):
        issue(f"{path}.authority_effect.replaces", "must contain material_id and revision_ref")
    if not _iso8601(value.get("occurred_at")):
        issue(f"{path}.occurred_at", "must be an ISO-8601 timestamp")
    if not _non_empty(value.get("idempotency_key")):
        issue(f"{path}.idempotency_key", "must be a non-empty string")
    if "extensions" in value and not _record(value["extensions"]):
        issue(f"{path}.extensions", "must be an object")


def assert_material_graph(input: object) -> JsonObject:
    result = validate_material_graph(input)
    if not result.ok:
        detail = "; ".join(f"{item.path}: {item.message}" for item in result.issues[:8])
        raise MaterialGraphValidationError(f"Invalid Material Graph: {detail}")
    return input  # type: ignore[return-value]


def material_map(graph: Mapping[str, Any]) -> dict[str, JsonObject]:
    return {material["id"]: material for material in graph["materials"]}


def resolve_view(
    graph: Mapping[str, Any],
    view_id: str,
    *,
    mode: ViewMode = "expanded",
    max_depth: int = 4,
    max_items: int = 200,
) -> JsonObject:
    """Resolve direct View members or recursively expand nested Views safely."""
    graph = assert_material_graph(graph)
    if mode not in {"source", "expanded"}:
        raise ValueError("mode must be source or expanded")
    by_id = material_map(graph)
    view = by_id.get(view_id)
    if view is None or not _record(view.get("view")):
        raise TypeError(f"{view_id} is not a View Material")
    items: list[JsonObject] = []
    diagnostics: list[JsonObject] = []

    def visit(owner: JsonObject, member: Mapping[str, Any], depth: int, trail: set[str]) -> None:
        if len(items) >= max_items:
            diagnostics.append({"kind": "truncated", "at": owner["id"], "detail": f"max_items={max_items}"})
            return
        material = by_id.get(member["material_id"])
        if material is None:
            diagnostics.append({"kind": "missing", "at": owner["id"], "detail": member["material_id"]})
            return
        nested = material.get("view")
        if mode == "expanded" and _record(nested):
            if material["id"] in trail:
                diagnostics.append({"kind": "cycle", "at": owner["id"], "detail": material["id"]})
                return
            if depth >= max_depth:
                diagnostics.append({"kind": "truncated", "at": material["id"], "detail": f"max_depth={max_depth}"})
                return
            next_trail = trail | {material["id"]}
            for nested_member in nested["members"]:
                visit(material, nested_member, depth + 1, next_trail)
            return
        items.append({"material": material, "member": dict(member), "depth": depth, "owner_view_id": owner["id"]})

    for member in view["view"]["members"]:
        visit(view, member, 0, {view["id"]})
    item_ids = {item["material"]["id"] for item in items}
    relationships = [
        material
        for material in graph["materials"]
        if _record(material.get("relationship"))
        and material["relationship"]["from"] in item_ids
        and material["relationship"]["to"] in item_ids
    ]
    return {"contract_version": graph["contract_version"], "graph_id": graph["graph_id"], "view": view, "mode": mode, "items": items, "relationships": relationships, "diagnostics": diagnostics}


def slice_graph_for_view(graph: Mapping[str, Any], view_id: str) -> JsonObject:
    graph = assert_material_graph(graph)
    by_id = material_map(graph)
    if view_id not in by_id:
        raise TypeError(f"{view_id} is not a View Material")
    keep = {view_id}
    queue = [view_id]
    while queue:
        current = by_id.get(queue.pop(0))
        if current is None or not _record(current.get("view")):
            continue
        for member in current["view"]["members"]:
            if member["material_id"] not in keep:
                queue.append(member["material_id"])
            keep.add(member["material_id"])
    for material in graph["materials"]:
        edge = material.get("relationship")
        if _record(edge) and edge["from"] in keep and edge["to"] in keep:
            keep.add(material["id"])
    sliced = deepcopy(graph)
    sliced["active_view_id"] = view_id
    sliced["materials"] = [material for material in graph["materials"] if material["id"] in keep]
    return sliced


def canonical_manifest(graph: Mapping[str, Any]) -> JsonObject:
    """Return the stable semantic graph representation, excluding generated/payload data."""
    graph = assert_material_graph(graph)

    def clone_semantic(value: Any) -> Any:
        if isinstance(value, list):
            return [clone_semantic(item) for item in value]
        if _record(value):
            return {key: clone_semantic(value[key]) for key in sorted(value)}
        return value

    materials: list[JsonObject] = []
    for material in graph["materials"]:
        representations = [
            clone_semantic({key: value for key, value in representation.items() if key != "content"})
            for representation in material["representations"]
        ]
        entry: JsonObject = {
            "id": material["id"], "title": material["title"], "labels": sorted(material["labels"]),
            "revision_ref": clone_semantic(material["revision_ref"]),
            "representations": sorted(representations, key=lambda item: item["id"]),
            "relationship": clone_semantic(material["relationship"]) if material.get("relationship") else None,
            "view": None,
        }
        if _record(material.get("view")):
            entry["view"] = {
                "members": [clone_semantic(member) for member in material["view"]["members"]],
                "presentation": clone_semantic(
                    {
                        key: value
                        for key, value in material["view"].get("presentation", {}).items()
                        if key != "theme"
                    }
                )
                if material["view"].get("presentation")
                else None,
            }
        entry["formal_domain"] = clone_semantic(material["formal_domain"]) if material.get("formal_domain") else None
        entry["formalization"] = clone_semantic(material["formalization"]) if material.get("formalization") else None
        materials.append(entry)
    return {"contract_version": graph["contract_version"], "graph_id": graph["graph_id"], "active_view_id": graph.get("active_view_id"), "materials": sorted(materials, key=lambda item: item["id"])}
