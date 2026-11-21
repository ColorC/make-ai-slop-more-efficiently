"""Unified BOSS SIGHT entity index for @mentions and ultra search."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from omnicompany.core.config import omni_workspace_root

from .aggregator.plan_index_scanner import PlanIndexScanner

SKIP_DIRS = {"_archive", "node_modules", "_legacy", "venv", ".venv", ".git", "__pycache__"}
MENTION_RE = re.compile(r"@([A-Za-z_][\w-]*):([^\s@<>()\[\]{}，。；;、,]+)")
OMNI_URI_RE = re.compile(r"omni://[A-Za-z_][\w-]*(?:/[^\s<>()\[\]{}，。；;,]+)?")


@dataclass(frozen=True)
class EntityRecord:
    uri: str
    kind: str
    id: str
    display: str
    short_name: str
    title: str
    snippet: str = ""
    source: str = ""
    open_ref: dict[str, Any] | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["open_ref"] is None:
            data["open_ref"] = {}
        return data


def make_entity_uri(kind: str, entity_id: str) -> str:
    return f"omni://{kind}/{quote(entity_id, safe='')}"


def parse_entity_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "omni" or not parsed.netloc:
        raise ValueError(f"invalid omni uri: {uri}")
    entity_id = unquote(parsed.path.lstrip("/"))
    if not entity_id:
        raise ValueError(f"invalid omni uri without id: {uri}")
    return parsed.netloc, entity_id


def _workspace_root(ws: str | Path | None = None) -> Path:
    return Path(ws) if ws is not None else omni_workspace_root()


def _safe_read_text(path: Path, limit: int = 2000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _short_name(value: str, fallback: str = "entity") -> str:
    raw = (value or fallback).strip().replace("\\", "/")
    raw = raw.split("/")[-1] or raw
    raw = re.sub(r"^\[\d{4}-\d{2}-\d{2}\]", "", raw).strip()
    raw = raw.removesuffix(".md").removesuffix(".py").strip()
    return raw[:60] or fallback


def _record(
    *,
    kind: str,
    entity_id: str,
    title: str,
    source: str,
    snippet: str = "",
    open_ref: dict[str, Any] | None = None,
    path: str | None = None,
    display_kind: str | None = None,
) -> EntityRecord:
    short = _short_name(title or entity_id, fallback=kind)
    shown_kind = display_kind or kind
    return EntityRecord(
        uri=make_entity_uri(kind, entity_id),
        kind=kind,
        id=entity_id,
        display=f"@{shown_kind}:{short}",
        short_name=short,
        title=title or short,
        snippet=snippet,
        source=source,
        open_ref=open_ref,
        path=path,
    )


def _scan_projects(ws: Path) -> list[EntityRecord]:
    out: list[EntityRecord] = []
    root = ws / "docs" / "plans"
    if not root.is_dir():
        return out
    for project_md in sorted(root.glob("*/project.md")):
        try:
            category = project_md.parent.relative_to(root).as_posix()
            rel = project_md.relative_to(ws).as_posix()
        except ValueError:
            continue
        title = category
        snippet = _safe_read_text(project_md, 320)
        out.append(_record(
            kind="project",
            entity_id=category,
            title=title,
            source="docs/plans/project.md",
            snippet=snippet,
            open_ref={"type": "note", "id": f"plans/{category}/project"},
            path=rel,
        ))
    return out


def _scan_plans(ws: Path) -> list[EntityRecord]:
    out: list[EntityRecord] = []
    for entry in PlanIndexScanner(ws).scan():
        title = entry.title or entry.plan_id
        out.append(_record(
            kind="plan",
            entity_id=entry.plan_id,
            title=title,
            source="docs/plans",
            snippet=f"status={entry.status or 'unknown'} todo={entry.todo_done}/{entry.todo_total}",
            open_ref={"type": "plan", "id": entry.plan_id},
            path=entry.plan_path,
        ))
    return out


def _scan_notes(ws: Path) -> list[EntityRecord]:
    out: list[EntityRecord] = []
    docs = ws / "docs"
    if not docs.is_dir():
        return out
    for path in sorted(docs.rglob("*.md")):
        try:
            rel_docs = path.relative_to(docs)
        except ValueError:
            continue
        if _is_skipped(rel_docs):
            continue
        entity_id = rel_docs.with_suffix("").as_posix()
        rel = path.relative_to(ws).as_posix()
        title = path.stem
        out.append(_record(
            kind="file",
            entity_id=entity_id,
            title=title,
            source="docs",
            snippet=_safe_read_text(path, 260),
            open_ref={"type": "note", "id": entity_id},
            path=rel,
        ))
    return out


def _scan_source_catalogue(ws: Path) -> list[EntityRecord]:
    out: list[EntityRecord] = []
    packages = ws / "src" / "omnicompany" / "packages"
    if not packages.is_dir():
        return out
    seen: set[tuple[str, str]] = set()
    for path in sorted(packages.rglob("*.py")):
        try:
            rel = path.relative_to(packages)
        except ValueError:
            continue
        if _is_skipped(rel):
            continue
        rel_id = rel.with_suffix("").as_posix()
        name = path.stem
        kind: str | None = None
        if name in {"materials", "formats"}:
            kind = "material"
        elif name.startswith("team"):
            probe = _safe_read_text(path, 1200)
            if "TeamSpec" in probe and "build_" in probe:
                kind = "team"
        elif "workers" in rel.parts and name != "__init__":
            kind = "worker"
        if not kind:
            continue
        key = (kind, rel_id)
        if key in seen:
            continue
        seen.add(key)
        title = f"{rel.parent.as_posix().split('/')[-1]}.{name}" if kind == "material" else name
        out.append(_record(
            kind=kind,
            entity_id=rel_id,
            title=title,
            source="packages",
            snippet=rel.as_posix(),
            open_ref={"type": kind, "id": rel_id} if kind in {"material", "team", "worker"} else None,
            path=path.relative_to(ws).as_posix(),
        ))
    return out


def _scan_review_materials(_ws: Path) -> list[EntityRecord]:
    """审阅材料实体（2026-08-20 审阅库融合换源）：读共享核心 review_store 的画布投影。

    旧实现读 reviewstage MaterialStore（store.list()）。对齐旧口径：本地过滤内部治理件
    （旧 list() 默认 include_internal=False）。
    """
    out: list[EntityRecord] = []
    try:
        from omnicompany.packages.services._core import review_store

        from .cockpit import _row_is_internal

        materials = [
            row for row in review_store.list_materials(include_archived=False)
            if not _row_is_internal(row)
        ]
    except Exception:
        return out
    for m in materials:
        status = str(m.get("status") or "")
        tier = str(m.get("tier") or "")
        kind = str(m.get("kind") or "")
        out.append(_record(
            kind="review_material",
            entity_id=str(m.get("id") or ""),
            title=str(m.get("title") or ""),
            source="reviewstage",
            snippet=f"{kind} {tier} {status}",
            open_ref={"url": f"/review-stage?material={m.get('id')}"},
            path=m.get("file_relpath"),
            display_kind="material",
        ))
    return out


def _scan_cc_sessions(ws: Path) -> list[EntityRecord]:
    out: list[EntityRecord] = []
    path = ws / "data" / "cc_sessions.json"
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(data, dict):
        return out
    for sid, fields in data.items():
        if not isinstance(fields, dict):
            continue
        provider = str(fields.get("provider") or "session")
        cwd = str(fields.get("cwd") or "")
        title = fields.get("name") or fields.get("active_plan") or sid[-8:]
        out.append(_record(
            kind="cc_session",
            entity_id=sid,
            title=str(title),
            source="cc_sessions",
            snippet=f"{provider} cwd={cwd} alive={bool(fields.get('alive'))}",
            open_ref={"type": "cc_session", "id": sid},
            path=cwd or None,
            display_kind="session",
        ))
        if provider != "controller":
            out.append(_record(
                kind="subagent",
                entity_id=sid,
                title=str(title),
                source="cc_sessions",
                snippet=f"active_plan={fields.get('active_plan') or ''} alive={bool(fields.get('alive'))}",
                open_ref={"type": "cc_session", "id": sid},
                path=cwd or None,
            ))
    return out


def _static_records() -> list[EntityRecord]:
    commands = [
        ("worker.spawn", "omni worker spawn", "Spawn standalone/team worker"),
        ("worker.fork", "omni worker fork", "Fork a running worker for report"),
        ("review.submit", "omni review submit", "Submit material to reviewstage"),
        ("review.judge", "omni review judge", "Judge reviewstage material routing tier"),
        ("plan.audit", "omni plan audit", "Audit plans and missing todos"),
    ]
    out = [
        _record(
            kind="settings",
            entity_id="main",
            title="设置 / 系统信息",
            source="static",
            snippet="Dashboard settings and system info",
            open_ref={"type": "settings", "id": "main"},
        ),
        _record(
            kind="material_registry",
            entity_id="main",
            title="任务材料全景",
            source="static",
            snippet="Semantic material registry for task context and execution boundaries",
            open_ref={"type": "material_registry", "id": "main"},
        )
    ]
    for entity_id, title, snippet in commands:
        out.append(_record(
            kind="command",
            entity_id=entity_id,
            title=title,
            source="static",
            snippet=snippet,
            open_ref={"command": title},
        ))
    return out


# 实体索引扫全工作区(docs 下 ~1000+ md 逐个读 + project/源码目录), 单次 ~数秒。驾驶舱 briefing /
# workflow-summary / material-registry / @搜索 都走它, 且一次 briefing 请求内会被调用 2-3 次 →
# 加载卡顿的主因(用户 2026-06-04 反馈"每次加载都很久")。加短 TTL 缓存: 收敛单请求内的重复调用、
# 加速窗口内的重复加载。docs/plans 变化慢, 短暂(默认 12s, 可 OMNI_ENTITY_INDEX_TTL 调)的陈旧可接受;
# 审阅队列/会话列表走各自的存储(快且实时), 不受此缓存影响。需要强制最新可 force_refresh=True。
_INDEX_CACHE: dict[str, tuple[float, list["EntityRecord"]]] = {}


def _index_ttl() -> float:
    try:
        return float(os.environ.get("OMNI_ENTITY_INDEX_TTL", "12"))
    except (TypeError, ValueError):
        return 12.0


def build_entity_index(ws: str | Path | None = None, *, force_refresh: bool = False) -> list[EntityRecord]:
    root = _workspace_root(ws)
    key = str(root.resolve())
    now = time.monotonic()
    if not force_refresh:
        cached = _INDEX_CACHE.get(key)
        if cached is not None and (now - cached[0]) < _index_ttl():
            return cached[1]

    records: list[EntityRecord] = []
    records.extend(_scan_plans(root))
    records.extend(_scan_projects(root))
    records.extend(_scan_notes(root))
    records.extend(_scan_source_catalogue(root))
    records.extend(_scan_review_materials(root))
    records.extend(_scan_cc_sessions(root))
    records.extend(_static_records())

    dedup: dict[str, EntityRecord] = {}
    for rec in records:
        dedup.setdefault(rec.uri, rec)
    result = list(dedup.values())
    _INDEX_CACHE[key] = (now, result)
    return result


def invalidate_entity_index_cache() -> None:
    """清空实体索引缓存(写 plan/标准/材料后想立刻反映可调)。"""
    _INDEX_CACHE.clear()


def _score(rec: EntityRecord, query: str) -> int:
    q = query.strip().lower()
    if not q:
        return 1
    if q.startswith("@"):
        q = q[1:]
    if q.startswith("omni://"):
        return 1000 if rec.uri.lower() == q else 0
    kind_filter = ""
    term = q
    if ":" in q:
        kind_filter, term = q.split(":", 1)
        if kind_filter and kind_filter not in {rec.kind.lower(), rec.display.split(":", 1)[0].lstrip("@").lower()}:
            return 0
    hay_title = f"{rec.display} {rec.short_name} {rec.title}".lower()
    hay_rest = f"{rec.id} {rec.snippet} {rec.path or ''} {rec.source}".lower()
    if term and rec.display.lower() == f"@{q}":
        return 900
    if term and term == rec.short_name.lower():
        return 800
    if term and term in hay_title:
        return 600
    if term and term in hay_rest:
        return 300
    if not term and kind_filter:
        return 100
    return 0


def search_entities(
    query: str = "",
    *,
    kind: str | None = None,
    limit: int = 50,
    ws: str | Path | None = None,
) -> list[dict[str, Any]]:
    records = build_entity_index(ws)
    if kind:
        records = [r for r in records if r.kind == kind]
    scored = [(r, _score(r, query)) for r in records]
    if query.strip():
        scored = [(r, s) for r, s in scored if s > 0]
    scored.sort(key=lambda rs: (-rs[1], rs[0].kind, rs[0].display.lower(), rs[0].id))
    capped = max(1, min(int(limit), 100))
    return [dict(r.to_dict(), score=s) for r, s in scored[:capped]]


def resolve_entity_uri(uri: str, *, ws: str | Path | None = None) -> dict[str, Any] | None:
    kind, entity_id = parse_entity_uri(uri)
    target = make_entity_uri(kind, entity_id)
    for rec in build_entity_index(ws):
        if rec.uri == target:
            return rec.to_dict()
    return None


def _normalize_explicit_mention(item: Any, records_by_uri: dict[str, EntityRecord]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    uri = str(item.get("uri") or "")
    if not uri:
        return None
    try:
        parse_entity_uri(uri)
    except ValueError:
        return None
    rec = records_by_uri.get(uri)
    if rec:
        return {
            "uri": rec.uri,
            "display": str(item.get("display") or rec.display),
            "kind": rec.kind,
            "id": rec.id,
            "title": rec.title,
        }
    return {
        "uri": uri,
        "display": str(item.get("display") or uri),
        "kind": item.get("kind"),
        "id": item.get("id"),
        "title": item.get("title"),
        "unresolved": True,
    }


def extract_entity_mentions(
    text: str,
    *,
    explicit_mentions: list[Any] | None = None,
    ws: str | Path | None = None,
) -> list[dict[str, Any]]:
    records = build_entity_index(ws)
    by_uri = {r.uri: r for r in records}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in explicit_mentions or []:
        normalized = _normalize_explicit_mention(item, by_uri)
        if normalized and normalized["uri"] not in seen:
            seen.add(normalized["uri"])
            out.append(normalized)

    for uri in OMNI_URI_RE.findall(text or ""):
        rec = by_uri.get(uri)
        key = uri
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "uri": uri,
            "display": rec.display if rec else uri,
            "kind": rec.kind if rec else None,
            "id": rec.id if rec else None,
            "title": rec.title if rec else None,
            "unresolved": rec is None,
        })

    by_display: dict[tuple[str, str], list[EntityRecord]] = {}
    for rec in records:
        display_kind = rec.display.split(":", 1)[0].lstrip("@")
        keys = {
            (display_kind.lower(), rec.short_name.lower()),
            (rec.kind.lower(), rec.short_name.lower()),
        }
        for key in keys:
            by_display.setdefault(key, []).append(rec)

    for kind, short in MENTION_RE.findall(text or ""):
        key = (kind.lower(), short.lower())
        candidates = by_display.get(key, [])
        display = f"@{kind}:{short}"
        if len(candidates) == 1:
            rec = candidates[0]
            if rec.uri not in seen:
                seen.add(rec.uri)
                out.append({
                    "uri": rec.uri,
                    "display": display,
                    "kind": rec.kind,
                    "id": rec.id,
                    "title": rec.title,
                })
        elif len(candidates) > 1:
            ambiguous_key = f"ambiguous:{display}"
            if ambiguous_key not in seen:
                seen.add(ambiguous_key)
                out.append({
                    "display": display,
                    "ambiguous": True,
                    "candidates": [c.uri for c in candidates[:10]],
                })
        else:
            unresolved_key = f"unresolved:{display}"
            if unresolved_key not in seen:
                seen.add(unresolved_key)
                out.append({"display": display, "unresolved": True})
    return out


def normalize_comment_target(
    content: str,
    target: dict[str, Any] | None,
    *,
    ws: str | Path | None = None,
) -> dict[str, Any]:
    normalized = dict(target or {})
    explicit = normalized.get("mentions")
    mentions = extract_entity_mentions(
        content,
        explicit_mentions=explicit if isinstance(explicit, list) else None,
        ws=ws,
    )
    if mentions:
        normalized["mentions"] = mentions
    elif "mentions" in normalized:
        normalized.pop("mentions", None)
    return normalized


__all__ = [
    "EntityRecord",
    "build_entity_index",
    "extract_entity_mentions",
    "make_entity_uri",
    "normalize_comment_target",
    "parse_entity_uri",
    "resolve_entity_uri",
    "search_entities",
]
