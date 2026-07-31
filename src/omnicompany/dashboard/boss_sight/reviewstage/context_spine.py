"""Read-only, authority-labelled context projection for a review Material.

The Context Spine does not persist another metadata envelope.  It joins the
existing owners at read time:

* MaterialStore owns material identity, scope, version and lineage.
* ReviewContext owns review profile, schema and typed review references.
* cc_session_bindings.json owns conversation/session attribution.
* Material.extra is exposed only as legacy producer/source declarations.

Missing values are made explicit for an opened material, but this module never
installs hooks or emits reminders.  Submission-time reminders remain owned by
``capabilities.py`` and are resolved only for an explicit event.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from omnicompany.packages.services._core.identity import all_session_bindings

from .store import Material, MaterialStore


CONTEXT_SPINE_VERSION = 1

_SOURCE_EXTRA_KEYS = (
    "source_uri",
    "source_url",
    "source_path",
    "from_path",
    "doc",
    "doc_path",
    "web_source",
    "video_source",
    "source_manifest",
    "live_url",
    "aigc_lab_url",
    "aigc_lab",
)
_PRODUCER_EXTRA_KEYS = (
    "producer_provider",
    "provider",
    "generation_provider",
    "producer_model",
    "model",
    "model_id",
    "generation_model",
    "agent_framework",
    "framework",
    "generator",
    "generator_version",
    "run_id",
    "generation_run_id",
    "batch_id",
    "asset_run_id",
    "ui_run_id",
    "generation_task_id",
    "prompt_id",
    "prompt_path",
    "liclick_backend",
)


def canonical_review_ref(material_id: str) -> str:
    """Return the registered unified-reference form for a review material."""
    return f"omni://review/{quote(material_id, safe='')}"


def canonical_plan_ref(plan_id: str) -> str:
    return f"omni://plan/{quote(plan_id, safe='')}"


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    """Keep the projection useful without copying arbitrary large extra blobs."""
    if depth >= 3:
        return str(value)[:240]
    if isinstance(value, str):
        return value if len(value) <= 800 else value[:800] + "…"
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, Mapping):
        items = list(value.items())[:24]
        return {
            str(key): _compact_value(item, depth=depth + 1)
            for key, item in items
        }
    if isinstance(value, (list, tuple, set)):
        return [_compact_value(item, depth=depth + 1) for item in list(value)[:24]]
    return str(value)[:800]


def _field(
    key: str,
    label: str,
    value: Any,
    *,
    source: str,
    authority: str,
    derived: bool = False,
) -> dict[str, Any]:
    present = _has_value(value)
    return {
        "key": key,
        "label": label,
        "value": _compact_value(value) if present else None,
        "status": ("derived" if derived else "recorded") if present else "unrecorded",
        "source": source,
        "authority": authority,
        "authoritative": present and authority in {
            "MaterialStore",
            "ReviewContext",
            "session_binding_ledger",
            "unified_reference",
        },
    }


def _declared_extra(extra: Mapping[str, Any], keys: Iterable[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _compact_value(extra[key])}
        for key in keys
        if _has_value(extra.get(key))
    ]


def _content_hash(store: MaterialStore, material: Material) -> str | None:
    digest = sha256()
    if material.inline_content is not None:
        digest.update(material.inline_content.encode("utf-8"))
        return digest.hexdigest()
    path = store.resolve_file_path(material)
    if path is None:
        return None
    try:
        with Path(path).open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _material_sessions(
    material_id: str,
    bindings: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for trace_id, binding in bindings.items():
        records = binding.get("records") or []
        match = next(
            (
                record
                for record in records
                if isinstance(record, Mapping)
                and record.get("kind") == "review_material"
                and str(record.get("id") or "") == material_id
            ),
            None,
        )
        if match is None:
            continue
        session_id = binding.get("session_id") or binding.get("claude_session_id")
        sessions.append({
            "trace_id": str(binding.get("trace_id") or trace_id),
            "provider": binding.get("provider"),
            "session_id": session_id,
            "conversation_id": binding.get("conversation_id"),
            "pty_id": binding.get("pty_id"),
            "active_plan": binding.get("active_plan"),
            "project": binding.get("project"),
            "task_id": binding.get("task_id"),
            "cwd": binding.get("cwd"),
            "binding_source": binding.get("source"),
            "recorded_at": match.get("ts"),
            "updated_at": binding.get("updated_at"),
        })
    sessions.sort(
        key=lambda item: str(item.get("recorded_at") or item.get("updated_at") or ""),
        reverse=True,
    )
    return sessions


def _lineage_relationships(material: Material) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    links = material.links or {}
    parent = links.get("parent")
    if parent:
        relationships.append({
            "relation": "parent",
            "target_type": "review",
            "target_id": str(parent),
            "target_ref": canonical_review_ref(str(parent)),
            "authority": "MaterialStore",
            "source": "material.links.parent",
        })
    for relation in ("supersedes", "related"):
        values = links.get(relation) or []
        if isinstance(values, str):
            values = [values]
        for target in values:
            if not target:
                continue
            relationships.append({
                "relation": relation,
                "target_type": "review",
                "target_id": str(target),
                "target_ref": canonical_review_ref(str(target)),
                "authority": "MaterialStore",
                "source": f"material.links.{relation}",
            })
    return relationships


def build_material_context_spine(
    store: MaterialStore,
    material: Material,
    *,
    bindings: Mapping[str, Mapping[str, Any]] | None = None,
    include_content_hash: bool = True,
) -> dict[str, Any]:
    """Join all existing context authorities for one material."""
    binding_store = bindings if bindings is not None else all_session_bindings()
    sessions = _material_sessions(material.id, binding_store)
    extra = material.extra or {}
    review_context = material.review_context
    review_dict = review_context.to_dict() if review_context is not None else {}
    review_refs = list(review_dict.get("references") or [])
    source_refs = [
        ref for ref in review_refs
        if ref.get("relation") in {"source", "evidence", "external_surface"}
    ]
    declared_sources = _declared_extra(extra, _SOURCE_EXTRA_KEYS)
    declared_producer = _declared_extra(extra, _PRODUCER_EXTRA_KEYS)
    content_hash = _content_hash(store, material) if include_content_hash else None
    plan_id = str(material.source_plan_id or "").strip()

    sections = [
        {
            "id": "source",
            "label": "来源",
            "fields": [
                _field(
                    "canonical_ref",
                    "材料引用",
                    canonical_review_ref(material.id),
                    source="material.id",
                    authority="unified_reference",
                    derived=True,
                ),
                _field(
                    "storage_path",
                    "审阅副本",
                    material.file_relpath,
                    source="material.file_relpath",
                    authority="MaterialStore",
                ),
                _field(
                    "origin_references",
                    "来源与证据引用",
                    source_refs,
                    source="material.review_context.references",
                    authority="ReviewContext",
                ),
                _field(
                    "declared_sources",
                    "旧材料来源声明",
                    declared_sources,
                    source="material.extra",
                    authority="Material.extra (legacy)",
                ),
                _field(
                    "content_sha256",
                    "审阅内容指纹",
                    content_hash,
                    source="material.inline_content|file_relpath",
                    authority="derived",
                    derived=True,
                ),
            ],
        },
        {
            "id": "scope",
            "label": "归属",
            "fields": [
                _field(
                    "project",
                    "项目",
                    material.project,
                    source="material.project",
                    authority="MaterialStore",
                ),
                _field(
                    "plan",
                    "计划",
                    {"id": plan_id, "ref": canonical_plan_ref(plan_id)} if plan_id else None,
                    source="material.source_plan_id",
                    authority="MaterialStore",
                ),
                _field(
                    "track",
                    "轨道 / 阶段",
                    material.track,
                    source="material.track",
                    authority="MaterialStore",
                ),
                _field(
                    "subject",
                    "主题 / 主体",
                    {
                        "id": material.subject_id,
                        "type": material.subject_type or "subject",
                    } if material.subject_id else None,
                    source="material.subject_id|subject_type",
                    authority="MaterialStore",
                ),
                _field(
                    "task_bindings",
                    "任务",
                    list(dict.fromkeys(
                        str(item["task_id"])
                        for item in sessions
                        if item.get("task_id")
                    )),
                    source="cc_session_bindings.task_id",
                    authority="session_binding_ledger",
                ),
            ],
        },
        {
            "id": "producer",
            "label": "生产过程",
            "fields": [
                _field(
                    "subagent",
                    "生产 Agent",
                    material.source_subagent_id,
                    source="material.source_subagent_id",
                    authority="MaterialStore",
                ),
                _field(
                    "sessions",
                    "会话 / 对话",
                    sessions,
                    source="cc_session_bindings.records[kind=review_material]",
                    authority="session_binding_ledger",
                ),
                _field(
                    "declared_producer",
                    "模型 / 框架 / 运行声明",
                    declared_producer,
                    source="material.extra",
                    authority="Material.extra (legacy)",
                ),
            ],
        },
        {
            "id": "review",
            "label": "审阅合同",
            "fields": [
                _field(
                    "carrier",
                    "载体",
                    _enum_value(material.kind),
                    source="material.kind",
                    authority="MaterialStore",
                ),
                _field(
                    "tier",
                    "审阅级别",
                    _enum_value(material.tier),
                    source="material.tier",
                    authority="MaterialStore",
                ),
                _field(
                    "status",
                    "裁决状态",
                    _enum_value(material.status),
                    source="material.status",
                    authority="MaterialStore",
                ),
                _field(
                    "profile",
                    "审阅场景",
                    review_dict.get("profile_id"),
                    source="material.review_context.profile_id",
                    authority="ReviewContext",
                ),
                _field(
                    "schema",
                    "专用组件 schema",
                    review_dict.get("schema_id"),
                    source="material.review_context.schema_id",
                    authority="ReviewContext",
                ),
                _field(
                    "routing",
                    "场景路由依据",
                    review_dict.get("resolution"),
                    source="material.review_context.resolution",
                    authority="ReviewContext",
                ),
                _field(
                    "references",
                    "全部审阅引用",
                    review_refs,
                    source="material.review_context.references",
                    authority="ReviewContext",
                ),
            ],
        },
        {
            "id": "lineage",
            "label": "版本与谱系",
            "fields": [
                _field(
                    "version_family",
                    "版本族",
                    material.version_family,
                    source="material.version_family",
                    authority="MaterialStore",
                ),
                _field(
                    "version",
                    "材料版本",
                    material.version,
                    source="material.version",
                    authority="MaterialStore",
                ),
                _field(
                    "revision",
                    "主体修订",
                    material.revision,
                    source="material.revision",
                    authority="MaterialStore",
                ),
                _field(
                    "links",
                    "谱系关系",
                    _lineage_relationships(material),
                    source="material.links",
                    authority="MaterialStore",
                ),
            ],
        },
    ]

    relationships = _lineage_relationships(material)
    if plan_id:
        relationships.append({
            "relation": "scoped_by",
            "target_type": "plan",
            "target_id": plan_id,
            "target_ref": canonical_plan_ref(plan_id),
            "authority": "MaterialStore",
            "source": "material.source_plan_id",
        })
    for session in sessions:
        relationships.append({
            "relation": "produced_in",
            "target_type": "session",
            "target_id": session.get("session_id") or session["trace_id"],
            "provider": session.get("provider"),
            "trace_id": session["trace_id"],
            "conversation_id": session.get("conversation_id"),
            "authority": "session_binding_ledger",
            "source": "cc_session_bindings.records",
        })
    for ref in review_refs:
        relationships.append({
            "relation": ref.get("relation") or "related",
            "target_type": "reference",
            "target_ref": ref.get("target"),
            "label": ref.get("label") or "",
            "authority": "ReviewContext",
            "source": "material.review_context.references",
        })

    checks = {
        "source.origin": bool(source_refs or declared_sources),
        "scope.project": bool(material.project),
        "scope.plan": bool(plan_id),
        "scope.track": bool(material.track),
        "scope.subject_or_family": bool(material.subject_id or material.version_family),
        "producer.session": bool(sessions),
        "producer.identity": bool(material.source_subagent_id or declared_producer or sessions),
        "review.profile": bool(review_context),
        "lineage.version": material.version is not None,
    }
    missing = [key for key, present in checks.items() if not present]
    recorded = len(checks) - len(missing)

    return {
        "schema_version": CONTEXT_SPINE_VERSION,
        "material_id": material.id,
        "canonical_ref": canonical_review_ref(material.id),
        "authority": {
            "material": "MaterialStore",
            "review": "ReviewContext",
            "session": "cc_session_bindings.json",
            "legacy_extra": "display-only, non-authoritative",
        },
        "sections": sections,
        "relationships": relationships,
        "completeness": {
            "recorded": recorded,
            "expected": len(checks),
            "ratio": round(recorded / len(checks), 4),
            "missing": missing,
            "delivery": "on_material_open",
            "emits_reminders": False,
        },
    }


__all__ = [
    "CONTEXT_SPINE_VERSION",
    "build_material_context_spine",
    "canonical_plan_ref",
    "canonical_review_ref",
]
