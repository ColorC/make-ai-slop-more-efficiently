"""Semantic material registry for BOSS SIGHT task context and execution boundary."""

from __future__ import annotations

import os
import re
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from omnicompany.core.config import omni_workspace_root
from omnicompany.core.plans_catalogue import parse_plan_frontmatter
from omnicompany.packages.services._core.omnicompany.formats import PLAN, register_formats
from omnicompany.packages.services._core.omnicompany.material_events import query_material_events
from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.format import Format, create_builtin_registry

from .aggregator.plan_index_scanner import PlanIndexEntry, PlanIndexScanner
from .entity_registry import build_entity_index, make_entity_uri

CONTEXT_EXTS = {".md", ".txt", ".yaml", ".yml", ".json"}
SKIP_PARTS = {"_archive", "_legacy", ".git", "node_modules", "venv", ".venv", "__pycache__", "static"}

ROLE_BY_KIND = {
    "roadmap": "direction",
    "plan": "direction",
    "project": "direction",
    "decision": "direction",
    "guard": "boundary",
    "policy": "boundary",
    "standard": "boundary",
    "template": "reference",
    "prompt": "reference",
    "example": "reference",
    "progress": "progress",
    "progress_note": "progress",
    "handoff": "progress",
    "report": "progress",
    "audit": "progress",
    "reflection": "progress",
    "review_material": "review",
    "material_definition": "reference",
    "worker": "executor",
    "team": "executor",
    "subagent": "executor",
}

ROLE_PRIORITY = {
    "direction": 0,
    "boundary": 1,
    "progress": 2,
    "review": 3,
    "reference": 4,
    "executor": 5,
    "project_asset": 6,
}

FORMAT_TAG_KIND = {
    "content.plan": "plan",
    "content.project": "project",
    "content.progress": "progress",
    "content.capture": "capture",
    "content.review": "review_material",
}

FORMAT_TAG_ROLE = {
    "content.plan": "direction",
    "content.project": "direction",
    "content.progress": "progress",
    "content.capture": "progress",
    "content.review": "review",
}


@dataclass(frozen=True)
class MaterialRelation:
    kind: str
    id: str
    label: str
    uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaterialRegistryItem:
    uri: str
    id: str
    title: str
    kind: str
    role: str
    layer: str
    status: str | None = None
    display: str = ""
    source: str = ""
    path: str | None = None
    snippet: str = ""
    open_ref: dict[str, Any] = field(default_factory=dict)
    entity_uri: str | None = None
    relations: list[MaterialRelation] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    updated_at: str | None = None
    format_id: str | None = None
    event_id: str | None = None
    trace_id: str | None = None
    event_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["relations"] = [r.to_dict() for r in self.relations]
        return data


def _workspace_root(ws: str | Path | None = None) -> Path:
    return Path(ws) if ws is not None else omni_workspace_root()


def _material_uri(material_id: str) -> str:
    return f"omni://material/{quote(material_id, safe='')}"


def _short_name(value: str) -> str:
    raw = (value or "material").replace("\\", "/").split("/")[-1]
    raw = re.sub(r"^\[\d{4}-\d{2}-\d{2}\]", "", raw).strip()
    return raw.removesuffix(".md").removesuffix(".yaml").removesuffix(".yml").removesuffix(".json")[:60] or "material"


def _safe_read(path: Path, limit: int = 900) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _safe_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _is_skipped(path: Path) -> bool:
    return any(part.lower() in SKIP_PARTS for part in path.parts)


def _walk_files(root: Path):
    """Yield files without descending into known generated/vendor trees."""

    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(
            name for name in directories if name.lower() not in SKIP_PARTS
        )
        for filename in sorted(filenames):
            yield Path(current) / filename


def _plan_id_for_path(rel: Path) -> str | None:
    parts = rel.parts
    if len(parts) >= 4 and parts[0] == "docs" and parts[1] == "plans":
        return f"{parts[2]}/{parts[3]}"
    return None


def _relations_for_path(rel: Path, *, plan_id: str | None = None) -> list[MaterialRelation]:
    relations: list[MaterialRelation] = []
    pid = plan_id or _plan_id_for_path(rel)
    if pid:
        relations.append(MaterialRelation(kind="plan", id=pid, label="belongs_to_plan", uri=make_entity_uri("plan", pid)))
        project = pid.split("/", 1)[0]
        relations.append(MaterialRelation(kind="project", id=project, label="belongs_to_project", uri=make_entity_uri("project", project)))
    elif len(rel.parts) >= 3 and rel.parts[0] == "docs" and rel.parts[1] == "plans":
        project = rel.parts[2]
        relations.append(MaterialRelation(kind="project", id=project, label="belongs_to_project", uri=make_entity_uri("project", project)))
    return relations


def _doc_kind(rel: Path) -> str | None:
    parts_l = [p.lower() for p in rel.parts]
    name = rel.name.lower()
    stem = rel.stem.lower()
    joined = "/".join(parts_l)

    if rel.suffix.lower() not in CONTEXT_EXTS:
        return None
    if _is_skipped(rel):
        return None
    if name in {"plan.md", "brief.md"} and len(rel.parts) >= 4 and rel.parts[0] == "docs" and rel.parts[1] == "plans":
        return None
    if name == "project.md":
        return None
    if "prompt" in stem or "prompts" in parts_l:
        return "prompt"
    if "guard" in stem or "guard" in parts_l:
        return "guard"
    if "policy" in stem or "policy" in parts_l:
        return "policy"
    if "template" in stem or "templates" in parts_l:
        return "template"
    if "standard" in stem or "standards" in parts_l:
        return "standard"
    if "roadmap" in stem or "master_roadmap" in stem:
        return "roadmap"
    if "decision" in stem or "roadmap-reconverge" in joined or "action_plan" in stem:
        return "decision"
    if "progress" in stem:
        return "progress_note"
    if "handoff" in stem:
        return "handoff"
    if "reflection" in stem or "retrospective" in stem or "critique" in stem:
        return "reflection"
    if "audit" in stem:
        return "audit"
    if "report" in stem or (len(rel.parts) >= 2 and rel.parts[0] == "docs" and rel.parts[1] == "reports"):
        return "report"
    if "example" in stem or "examples" in parts_l:
        return "example"
    return None


def _role_for_kind(kind: str) -> str:
    return ROLE_BY_KIND.get(kind, "project_asset")


def _layer_for_kind(kind: str) -> str:
    return "executor" if _role_for_kind(kind) == "executor" else "context"


def _status_for_path(path: Path, kind: str) -> str | None:
    if kind in {"plan", "project", "roadmap", "decision", "progress", "handoff", "audit", "report", "standard", "guard", "template", "prompt"}:
        status = parse_plan_frontmatter(path).get("status")
        return str(status) if status else None
    return None


def _make_item(
    *,
    material_id: str,
    title: str,
    kind: str,
    source: str,
    path: str | None,
    snippet: str = "",
    status: str | None = None,
    open_ref: dict[str, Any] | None = None,
    entity_uri: str | None = None,
    relations: list[MaterialRelation] | None = None,
    updated_at: str | None = None,
    tags: list[str] | None = None,
    role: str | None = None,
    layer: str | None = None,
    format_id: str | None = None,
    event_id: str | None = None,
    trace_id: str | None = None,
    event_source: str | None = None,
) -> MaterialRegistryItem:
    role_v = role or _role_for_kind(kind)
    return MaterialRegistryItem(
        uri=_material_uri(material_id),
        id=material_id,
        title=title or _short_name(material_id),
        kind=kind,
        role=role_v,
        layer=layer or ("executor" if role_v == "executor" else _layer_for_kind(kind)),
        status=status,
        display=f"@material:{_short_name(title or material_id)}",
        source=source,
        path=path,
        snippet=snippet,
        open_ref=open_ref or {},
        entity_uri=entity_uri,
        relations=relations or [],
        tags=tags or [],
        updated_at=updated_at,
        format_id=format_id,
        event_id=event_id,
        trace_id=trace_id,
        event_source=event_source,
    )


def _company_material_formats() -> dict[str, Format]:
    registry = create_builtin_registry()
    register_formats(registry)
    return {
        fmt.id: fmt
        for fmt in registry.all_formats()
        if fmt.id.startswith("omni.") and "omni.material" in fmt.tags
    }


def _format_kind(fmt: Format) -> str:
    for tag in fmt.tags:
        if tag in FORMAT_TAG_KIND:
            return FORMAT_TAG_KIND[tag]
    return fmt.id.split(".", 1)[-1].replace("-", "_")


def _format_role(fmt: Format) -> str:
    for tag in fmt.tags:
        if tag in FORMAT_TAG_ROLE:
            return FORMAT_TAG_ROLE[tag]
    return _role_for_kind(_format_kind(fmt))


def _format_layer(fmt: Format) -> str:
    return "executor" if _format_role(fmt) == "executor" else "context"


def _merge_tags(*groups: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for group in groups:
        for tag in group:
            if tag and tag not in out:
                out.append(tag)
    return out


def _relative_or_raw(root: Path, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    try:
        if path.is_absolute():
            return path.relative_to(root).as_posix()
    except ValueError:
        pass
    return text.replace("\\", "/")


def _items_from_company_formats(formats: dict[str, Format]) -> list[MaterialRegistryItem]:
    out: list[MaterialRegistryItem] = []
    for fmt in formats.values():
        out.append(_make_item(
            material_id=f"format/{fmt.id}",
            title=fmt.name,
            kind="material_definition",
            source="format_registry",
            path=None,
            snippet=fmt.description,
            tags=_merge_tags(tuple(fmt.tags), (fmt.id,)),
            format_id=fmt.id,
        ))
    return out


def _event_relation(kind: str, payload: dict[str, Any]) -> list[MaterialRelation]:
    relations: list[MaterialRelation] = []
    if kind == "progress":
        ref_type = str(payload.get("ref_type") or "")
        ref_id = str(payload.get("ref_id") or "")
        if ref_type in {"plan", "project"} and ref_id:
            relations.append(MaterialRelation(
                kind=ref_type,
                id=ref_id,
                label=f"progress_for_{ref_type}",
                uri=make_entity_uri(ref_type, ref_id),
            ))
    plan_id = str(payload.get("source_plan_id") or payload.get("plan_id") or "")
    if plan_id and kind not in {"plan", "progress"}:
        relations.append(MaterialRelation(kind="plan", id=plan_id, label="belongs_to_plan", uri=make_entity_uri("plan", plan_id)))
    subagent_id = str(payload.get("source_subagent_id") or "")
    if subagent_id:
        relations.append(MaterialRelation(kind="subagent", id=subagent_id, label="produced_by", uri=make_entity_uri("subagent", subagent_id)))
    return relations


def _event_identity(root: Path, fmt: Format, event: FactoryEvent) -> tuple[str, str, str | None, str | None, dict[str, Any], str | None]:
    payload = dict(event.payload or {})
    kind = _format_kind(fmt)
    path = _relative_or_raw(root, payload.get("plan_path") or payload.get("path") or payload.get("saved_path") or payload.get("file_relpath"))
    status = payload.get("status")
    open_ref: dict[str, Any] = {}
    entity_uri: str | None = None

    if kind == "plan":
        identity = str(payload.get("plan_id") or payload.get("plan_path") or path or event.id)
        title = str(payload.get("title") or identity)
        if payload.get("plan_id"):
            open_ref = {"type": "plan", "id": payload.get("plan_id")}
            entity_uri = make_entity_uri("plan", str(payload.get("plan_id")))
    elif kind == "project":
        identity = str(payload.get("id") or event.id)
        title = str(payload.get("name") or identity)
        open_ref = {"type": "project", "id": identity}
        entity_uri = make_entity_uri("project", identity)
        if payload.get("deleted"):
            status = "deleted"
    elif kind == "progress":
        identity = str(payload.get("id") or f"{payload.get('ref_type')}/{payload.get('ref_id')}/{payload.get('created_at')}" or event.id)
        title = str(payload.get("text") or identity)[:120]
        open_ref = {"type": "progress", "id": identity}
    elif kind == "capture":
        identity = str(path or payload.get("saved_path") or event.id)
        title = str(payload.get("title") or payload.get("capture_kind") or _short_name(identity))
        open_ref = {"type": "capture", "path": path or identity}
    elif kind == "review_material":
        identity = str(payload.get("id") or event.id)
        title = str(payload.get("title") or identity)
        open_ref = {"url": f"/review-stage?material={identity}"}
        entity_uri = make_entity_uri("review_material", identity)
    else:
        identity = str(payload.get("id") or path or event.id)
        title = str(payload.get("title") or payload.get("name") or identity)
    return identity, title, path, str(status) if status else None, open_ref, entity_uri


def _items_from_material_events(root: Path, formats: dict[str, Format]) -> list[MaterialRegistryItem]:
    out: list[MaterialRegistryItem] = []
    for fmt in formats.values():
        for event in query_material_events(event_type=fmt.id, limit=1000):
            kind = _format_kind(fmt)
            identity, title, path, status, open_ref, entity_uri = _event_identity(root, fmt, event)
            material_id = f"{fmt.id}/{identity}"
            out.append(_make_item(
                material_id=material_id,
                title=title,
                kind=kind,
                source="events",
                path=path,
                status=status,
                snippet=str(event.payload.get("text") or event.payload.get("comment") or event.payload.get("desc") or "")[:520],
                open_ref=open_ref,
                entity_uri=entity_uri,
                relations=_event_relation(kind, dict(event.payload or {})),
                updated_at=event.timestamp.isoformat(),
                tags=_merge_tags(tuple(fmt.tags), tuple(event.tags), (fmt.id,)),
                role=_format_role(fmt),
                layer=_format_layer(fmt),
                format_id=fmt.id,
                event_id=event.id,
                trace_id=event.trace_id,
                event_source=event.source,
            ))
    return out


def _items_from_plans(ws: Path, plan_entries: list[PlanIndexEntry], formats: dict[str, Format]) -> list[MaterialRegistryItem]:
    out: list[MaterialRegistryItem] = []
    fmt = formats.get(PLAN.id)
    for entry in plan_entries:
        relations = [
            MaterialRelation(kind="project", id=entry.category, label="belongs_to_project", uri=make_entity_uri("project", entry.category))
        ]
        out.append(_make_item(
            material_id=entry.plan_path,
            title=entry.title or entry.plan_id,
            kind=_format_kind(fmt) if fmt else "plan",
            source="plan_index",
            path=entry.plan_path,
            status=entry.status,
            snippet=f"todo={entry.todo_done}/{entry.todo_total}",
            open_ref={"type": "plan", "id": entry.plan_id},
            entity_uri=make_entity_uri("plan", entry.plan_id),
            relations=relations,
            updated_at=entry.last_modified_ts,
            tags=_merge_tags(tuple(fmt.tags) if fmt else (), (PLAN.id, entry.category)),
            role=_format_role(fmt) if fmt else None,
            layer=_format_layer(fmt) if fmt else None,
            format_id=entry.format_id,
        ))
    return out


def _items_from_docs(ws: Path) -> list[MaterialRegistryItem]:
    out: list[MaterialRegistryItem] = []
    docs = ws / "docs"
    if not docs.is_dir():
        return out
    for path in _walk_files(docs):
        try:
            rel = path.relative_to(ws)
        except ValueError:
            continue
        kind = _doc_kind(rel)
        if kind is None:
            continue
        rel_posix = rel.as_posix()
        note_id = rel.with_suffix("").as_posix()
        title = parse_plan_frontmatter(path).get("title") or _short_name(path.name)
        open_ref = {"type": "note", "id": note_id} if path.suffix.lower() == ".md" else {}
        out.append(_make_item(
            material_id=rel_posix,
            title=str(title),
            kind=kind,
            source="docs",
            path=rel_posix,
            status=_status_for_path(path, kind),
            snippet=_safe_read(path, 520),
            open_ref=open_ref,
            entity_uri=make_entity_uri("file", note_id) if path.suffix.lower() == ".md" else None,
            relations=_relations_for_path(rel),
            updated_at=_safe_mtime(path),
            tags=[rel.parts[1] if len(rel.parts) > 1 else "docs"],
        ))
    return out


def _items_from_source_prompts(ws: Path) -> list[MaterialRegistryItem]:
    out: list[MaterialRegistryItem] = []
    src = ws / "src" / "omnicompany"
    if not src.is_dir():
        return out
    for path in _walk_files(src):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            rel = path.relative_to(ws)
        except ValueError:
            continue
        parts_l = [p.lower() for p in rel.parts]
        stem_l = path.stem.lower()
        if "prompt" not in stem_l and "prompts" not in parts_l and path.name.lower() != "skill.md":
            continue
        if _is_skipped(rel):
            continue
        rel_posix = rel.as_posix()
        relations: list[MaterialRelation] = []
        if "boss_sight" in parts_l and "controller" in parts_l:
            relations.append(MaterialRelation(kind="controller", id="main", label="configures_executor", uri=make_entity_uri("controller", "main")))
        out.append(_make_item(
            material_id=rel_posix,
            title=_short_name(path.name),
            kind="prompt",
            source="source_prompt",
            path=rel_posix,
            snippet=_safe_read(path, 520),
            open_ref={},
            entity_uri=None,
            relations=relations,
            updated_at=_safe_mtime(path),
            tags=["prompt"],
        ))
    return out


def _items_from_entities(ws: Path) -> list[MaterialRegistryItem]:
    out: list[MaterialRegistryItem] = []
    for rec in build_entity_index(ws):
        kind = rec.kind
        if kind == "material":
            continue
        elif kind in {"worker", "team", "subagent"}:
            reg_kind = kind
        elif kind == "review_material":
            continue
        else:
            continue
        out.append(_make_item(
            material_id=f"{reg_kind}/{rec.id}",
            title=rec.title,
            kind=reg_kind,
            source=rec.source,
            path=rec.path,
            status=None,
            snippet=rec.snippet,
            open_ref=rec.open_ref or {},
            entity_uri=rec.uri,
            relations=[],
            tags=[rec.kind],
        ))
    return out


def _is_deleted(item: MaterialRegistryItem) -> bool:
    return (item.status or "").lower() == "deleted"


def _dedup(items: list[MaterialRegistryItem]) -> list[MaterialRegistryItem]:
    by_id: dict[str, MaterialRegistryItem] = {}
    for item in items:
        prev = by_id.get(item.id)
        if prev is None:
            by_id[item.id] = item
            continue
        # 墓碑(status=deleted)对同一 id 权威获胜: 删过的项目不能被早先元数据更丰富的
        # 活跃记录夺回, 否则已删项目会在活跃视图里复活(幽灵项目)。
        prev_deleted = _is_deleted(prev)
        item_deleted = _is_deleted(item)
        if prev_deleted != item_deleted:
            if item_deleted:
                by_id[item.id] = item
            continue
        # 非 deleted 之间(或都 deleted): 仍按元数据丰富度取胜(open_ref + entity_uri + relations)。
        prev_score = bool(prev.open_ref) + bool(prev.entity_uri) + len(prev.relations)
        score = bool(item.open_ref) + bool(item.entity_uri) + len(item.relations)
        if score > prev_score:
            by_id[item.id] = item
    return list(by_id.values())


def _matches(item: MaterialRegistryItem, *, q: str, kind: str | None, role: str | None, layer: str | None, status: str | None) -> bool:
    if kind and item.kind != kind:
        return False
    if role and item.role != role:
        return False
    if layer and item.layer != layer:
        return False
    if status and (item.status or "unknown") != status:
        return False
    query = q.strip().lower()
    if not query:
        return True
    hay = " ".join([
        item.uri,
        item.id,
        item.title,
        item.kind,
        item.role,
        item.layer,
        item.status or "",
        item.source,
        item.path or "",
        item.snippet,
        " ".join(item.tags),
    ]).lower()
    return query in hay


def _sort_key(item: MaterialRegistryItem) -> tuple[int, int, str, str]:
    active_bias = 0 if (item.status or "").lower() in {"active", "in_progress", "in_progress_with_known_gaps"} else 1
    return (ROLE_PRIORITY.get(item.role, 99), active_bias, item.kind, item.title.lower())


def _counts(items: list[MaterialRegistryItem]) -> dict[str, dict[str, int]]:
    return {
        "by_kind": dict(Counter(i.kind for i in items)),
        "by_role": dict(Counter(i.role for i in items)),
        "by_layer": dict(Counter(i.layer for i in items)),
        "by_status": dict(Counter(i.status or "unknown" for i in items)),
    }


# 采集全部材料项需扫 plans + docs(逐个读 ~1000+ md)+ 源码 prompt + 实体索引 —— 实测 ~3.4s, 是驾驶舱
# briefing/workflow/material-registry 加载卡顿的主因(用户 2026-06-04 反馈"每次加载都很久")。
#
# 用 **stale-while-revalidate**: 一旦建好, 请求**永远立刻拿缓存返回**(~0.01s), 重扫永不挡在请求路径上;
# 缓存过期(TTL)后, 下次访问**触发后台线程刷新**, 请求仍拿当前(略旧)的立即返回, 刷新完下次即新。
# 这样无论加载间隔多久都恒快; 数据至多 ~TTL 旧。过滤/排序/截断仍每次按参数实时算。
# (审阅队列/会话列表走独立存储, 实时不受影响; 真要强制最新可 force_refresh=True 或 invalidate。)
_REGISTRY_ITEMS_CACHE: dict[str, tuple[float, list[MaterialRegistryItem]]] = {}
_REGISTRY_REFRESHING: set[str] = set()
_REGISTRY_LOCK = threading.Lock()
_REGISTRY_CONDITION = threading.Condition(_REGISTRY_LOCK)


def _registry_ttl() -> float:
    try:
        return float(os.environ.get("OMNI_ENTITY_INDEX_TTL", "30"))
    except (TypeError, ValueError):
        return 30.0


def _build_registry_items(root: Path) -> list[MaterialRegistryItem]:
    formats = _company_material_formats()
    plan_entries = PlanIndexScanner(root).scan()
    return _dedup([
        *_items_from_company_formats(formats),
        *_items_from_plans(root, plan_entries, formats),
        *_items_from_material_events(root, formats),
        *_items_from_docs(root),
        *_items_from_source_prompts(root),
        *_items_from_entities(root),
    ])


def _kick_registry_refresh(root: Path, key: str) -> None:
    with _REGISTRY_LOCK:
        if key in _REGISTRY_REFRESHING:
            return
        _REGISTRY_REFRESHING.add(key)

    def _refresh() -> None:
        try:
            items = _build_registry_items(root)
            with _REGISTRY_CONDITION:
                _REGISTRY_ITEMS_CACHE[key] = (time.monotonic(), items)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _REGISTRY_CONDITION:
                _REGISTRY_REFRESHING.discard(key)
                _REGISTRY_CONDITION.notify_all()

    threading.Thread(target=_refresh, name="material-registry-refresh", daemon=True).start()


def _gather_registry_items(root: Path, *, force_refresh: bool = False) -> list[MaterialRegistryItem]:
    key = str(root.resolve())
    now = time.monotonic()
    with _REGISTRY_CONDITION:
        cached = _REGISTRY_ITEMS_CACHE.get(key)
    if cached is not None and not force_refresh:
        if (now - cached[0]) >= _registry_ttl():
            _kick_registry_refresh(root, key)  # 过期: 后台刷新, 本次仍立即返回(略旧的)缓存
        return cached[1]
    # 首次(或强刷): 同步构建一次。进程启动有 prewarm 兜底, 用户基本撞不到这条冷路径。
    with _REGISTRY_CONDITION:
        while key in _REGISTRY_REFRESHING:
            _REGISTRY_CONDITION.wait(timeout=1.0)
            cached = _REGISTRY_ITEMS_CACHE.get(key)
            if cached is not None:
                return cached[1]
        _REGISTRY_REFRESHING.add(key)
    try:
        items = _build_registry_items(root)
        with _REGISTRY_CONDITION:
            _REGISTRY_ITEMS_CACHE[key] = (time.monotonic(), items)
        return items
    finally:
        with _REGISTRY_CONDITION:
            _REGISTRY_REFRESHING.discard(key)
            _REGISTRY_CONDITION.notify_all()


def invalidate_material_registry_cache() -> None:
    with _REGISTRY_CONDITION:
        _REGISTRY_ITEMS_CACHE.clear()


def build_material_registry(
    *,
    q: str = "",
    kind: str | None = None,
    role: str | None = None,
    layer: str | None = None,
    status: str | None = None,
    limit: int = 250,
    ws: str | Path | None = None,
) -> dict[str, Any]:
    root = _workspace_root(ws)
    items = list(_gather_registry_items(root))
    # 墓碑(已删项目)默认不进活跃视图; 仅当显式 status=deleted 查询时才放行。
    if (status or "").lower() != "deleted":
        items = [i for i in items if not _is_deleted(i)]
    items = [i for i in items if _matches(i, q=q, kind=kind, role=role, layer=layer, status=status)]
    items.sort(key=_sort_key)
    capped = max(1, min(int(limit), 500))
    visible = items[:capped]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": [i.to_dict() for i in visible],
        "total": len(items),
        "returned": len(visible),
        "counts": _counts(items),
        "filters": {
            "q": q,
            "kind": kind,
            "role": role,
            "layer": layer,
            "status": status,
            "limit": capped,
        },
        "summary": material_registry_summary(items),
    }


def material_registry_summary(items: list[MaterialRegistryItem] | list[dict[str, Any]]) -> dict[str, Any]:
    material_items: list[dict[str, Any]] = [i.to_dict() if isinstance(i, MaterialRegistryItem) else i for i in items]
    counts = {
        "by_kind": dict(Counter(str(i.get("kind") or "") for i in material_items)),
        "by_role": dict(Counter(str(i.get("role") or "") for i in material_items)),
        "by_layer": dict(Counter(str(i.get("layer") or "") for i in material_items)),
    }
    highlighted = [
        i for i in material_items
        if i.get("role") in {"direction", "boundary", "progress"} or i.get("status") in {"active", "in_progress", "in_progress_with_known_gaps"}
    ][:30]
    execution_boundaries = [
        i for i in material_items
        if i.get("kind") in {"guard", "policy", "standard"} or i.get("role") == "boundary"
    ][:30]
    executors = [i for i in material_items if i.get("layer") == "executor"][:30]
    return {
        "total": len(material_items),
        "counts": counts,
        "highlighted_items": highlighted,
        "execution_boundaries": execution_boundaries,
        "executors": executors,
    }


__all__ = ["MaterialRegistryItem", "build_material_registry", "material_registry_summary"]
