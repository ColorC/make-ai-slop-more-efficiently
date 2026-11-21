"""Project Material Wiki -> shared Material Core 0.2 read projection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import quote

from omnicompany.packages.services._core.material_core import assert_material_graph

from .adapters.markdown import WikiDocument
from .schema import MaterialWikiProfile


def _encoded(value: str) -> str:
    return quote(value, safe="")


def document_view_id(project_id: str, material_id: str) -> str:
    return f"view:material-wiki/{_encoded(project_id)}/document/{_encoded(material_id)}"


def category_view_id(project_id: str, path: tuple[str, ...]) -> str:
    return f"view:material-wiki/{_encoded(project_id)}/category/{'/'.join(map(_encoded, path))}"


def root_view_id(project_id: str) -> str:
    return f"view:material-wiki/{_encoded(project_id)}/root"


_SOURCE_ANCHOR_LINE = re.compile(r'^<a\s+[^>]*\bid=["\'][^"\']+["\'][^>]*>\s*</a>$', re.IGNORECASE)
_MARKDOWN_LEAD = re.compile(r"^(?:#{1,6}|>|[-*+]|\d+[.)])\s+")


def _markdown_summary(body: str) -> str:
    """Return the first human-readable line, not migration/source markup."""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or _SOURCE_ANCHOR_LINE.fullmatch(line):
            continue
        return _MARKDOWN_LEAD.sub("", line).strip()[:240]
    return ""


def _view_material(
    *,
    material_id: str,
    title: str,
    revision: dict[str, str],
    members: list[dict[str, object]],
    source_ref: str,
    parent_view_id: str | None,
    navigation: bool,
    mode: str,
) -> dict[str, object]:
    return {
        "id": material_id,
        "title": title,
        "labels": [],
        "revision_ref": revision,
        "representations": [
            {
                "id": "definition",
                "media_type": "application/vnd.omni.view+json",
                "role": "primary",
                "source_ref": {"kind": "material-wiki-profile", "ref": source_ref},
                "renderer_hint": "material-view",
            }
        ],
        "view": {"members": members, "presentation": {"mode": mode}},
        "metadata": {
            "parent_view_id": parent_view_id,
            "navigation": navigation,
        },
    }


_SUBMISSION_STATE_LABELS = {
    "unreviewed": "待看",
    "seen": "已看",
    "fully-passed": "通过",
    "not-fully-passed": "未过",
}


def _submission_material(
    *,
    canvas: str,
    session_id: str,
    card_id: str,
    title: str,
    state: str,
    submitted_at: str,
    reason: str | None,
) -> dict[str, object]:
    """会话画布提审卡 → Material Core 材料投影(置顶组在 root 视图最前消费)。"""
    state_label = _SUBMISSION_STATE_LABELS.get(state, state or "待看")
    summary_bits = [f"画布提审 · {state_label} · {submitted_at[:10]}"]
    if reason:
        summary_bits.append(str(reason)[:160])
    return {
        "id": f"submission:{canvas}:{card_id}",
        "title": title,
        "summary": " · ".join(summary_bits),
        "labels": ["pinned-submission", f"state:{state or 'unreviewed'}"],
        "revision_ref": {"provider": "session-canvas", "object": canvas, "revision": card_id},
        "representations": [
            {
                "id": "canvas",
                "media_type": "application/vnd.omni.session-canvas+json",
                "role": "primary",
                "title": "札记画布",
                "source_ref": {"kind": "session-canvas", "ref": f"{canvas}#{card_id}"},
            }
        ],
        "metadata": {
            "session_id": session_id,
            "card_id": card_id,
            "canvas": canvas,
            "state": state or "unreviewed",
            "submitted_at": submitted_at,
        },
    }


def build_material_wiki_graph(
    project_id: str,
    profile: MaterialWikiProfile,
    documents: Iterable[tuple[Path, WikiDocument]],
    pinned_submissions: Iterable[dict[str, object]] = (),
) -> dict[str, object]:
    rows = tuple(documents)
    generated = max(
        (datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) for path, _document in rows),
        default=datetime.now(timezone.utc),
    ).isoformat()
    profile_revision = {
        "provider": "material-wiki-profile",
        "object": profile.source.ref,
        "revision": str(profile.schema_version),
    }
    labels: dict[tuple[str, ...], str] = {}
    ranks: dict[tuple[str, ...], tuple[int, ...]] = {}

    def walk(nodes, parent: tuple[str, ...], rank: tuple[int, ...]) -> None:
        for index, node in enumerate(nodes):
            path = (*parent, node.id)
            labels[path] = node.label
            ranks[path] = (*rank, index)
            walk(node.children, path, (*rank, index))

    walk(profile.taxonomy, (), ())
    paths: set[tuple[str, ...]] = set()
    for _file, document in rows:
        for depth in range(1, len(document.placement_path) + 1):
            paths.add(tuple(document.placement_path[:depth]))
    ordered_paths = sorted(paths, key=lambda path: (ranks.get(path, (10_000,)), path))
    body_materials: list[dict[str, object]] = []
    document_views: list[dict[str, object]] = []
    relations: list[dict[str, object]] = []
    ids = {document.material_id for _path, document in rows}
    for _path, document in rows:
        version = document.current or document.head
        revision = {
            "provider": "material-wiki",
            "object": document.canonical_ref,
            "revision": version.version,
        }
        body_materials.append(
            {
                "id": document.material_id,
                "title": document.title,
                "summary": _markdown_summary(version.body),
                "labels": [f"status:{version.status}", *[f"category:{part}" for part in document.placement_path]],
                "revision_ref": revision,
                "representations": [
                    {
                        "id": "markdown",
                        "media_type": "text/markdown",
                        "role": "primary",
                        "title": "文档",
                        "source_ref": {
                            "kind": "material-wiki-api",
                            "ref": (
                                f"/api/boss-sight/material-wiki/projects/{_encoded(project_id)}"
                                f"/materials/{_encoded(document.material_id)}?version={_encoded(version.version)}"
                            ),
                        },
                        "renderer_hint": "wiki-core",
                    }
                ],
                "metadata": {
                    "placement_path": list(document.placement_path),
                    "status": version.status,
                    "canonical_ref": document.canonical_ref,
                },
            }
        )
        parent = category_view_id(project_id, tuple(document.placement_path))
        document_views.append(
            _view_material(
                material_id=document_view_id(project_id, document.material_id),
                title=document.title,
                revision=revision,
                members=[{"material_id": document.material_id, "group": "正文", "order": 0}],
                source_ref=document.canonical_ref,
                parent_view_id=parent,
                navigation=False,
                mode="document",
            )
        )
        relation_sets = {
            "supersedes": version.relations.supersedes,
            "derived_from": version.relations.derived_from,
            "related": version.relations.related,
        }
        for predicate, targets in relation_sets.items():
            for target in targets:
                if target.material_id not in ids:
                    continue
                relation_id = (
                    f"relation:material-wiki/{_encoded(document.material_id)}/"
                    f"{predicate}/{_encoded(target.material_id)}"
                )
                relations.append(
                    {
                        "id": relation_id,
                        "title": f"{document.title} {predicate} {target.material_id}",
                        "labels": ["material-relation"],
                        "revision_ref": revision,
                        "representations": [
                            {
                                "id": "evidence",
                                "media_type": "application/json",
                                "role": "evidence",
                                "source_ref": {
                                    "kind": "material-wiki-version",
                                    "ref": f"{document.canonical_ref}#version={version.version}",
                                },
                            }
                        ],
                        "relationship": {
                            "from": document.material_id,
                            "predicate": predicate,
                            "to": target.material_id,
                        },
                    }
                )

    category_views: list[dict[str, object]] = []
    for path in ordered_paths:
        child_paths = [candidate for candidate in ordered_paths if len(candidate) == len(path) + 1 and candidate[:-1] == path]
        child_documents = [document for _file, document in rows if tuple(document.placement_path) == path]
        members = [
            *[
                {"material_id": category_view_id(project_id, child), "group": "分类", "order": index}
                for index, child in enumerate(child_paths)
            ],
            *[
                {
                    "material_id": document_view_id(project_id, document.material_id),
                    "group": "文档",
                    "order": 10_000 + index,
                }
                for index, document in enumerate(sorted(child_documents, key=lambda item: (item.title, item.material_id)))
            ],
        ]
        parent = category_view_id(project_id, path[:-1]) if len(path) > 1 else root_view_id(project_id)
        category_views.append(
            _view_material(
                material_id=category_view_id(project_id, path),
                title=labels.get(path, path[-1]),
                revision=profile_revision,
                members=members,
                source_ref=f"{profile.source.ref}#path={'/'.join(path)}",
                parent_view_id=parent,
                navigation=True,
                mode="collection",
            )
        )
    roots = [path for path in ordered_paths if len(path) == 1]
    pinned_materials = [
        _submission_material(**pinned)
        for pinned in pinned_submissions
    ]
    root = _view_material(
        material_id=root_view_id(project_id),
        title=f"{project_id} 文档",
        revision=profile_revision,
        members=[
            # 置顶提交: 本会话画布提审卡投影, 排在 root 视图最前(契约内置顶, 非前端自造)。
            *[
                {"material_id": str(pinned["id"]), "group": "置顶", "order": -len(pinned_materials) + index}
                for index, pinned in enumerate(pinned_materials)
            ],
            *[
                {"material_id": category_view_id(project_id, path), "group": "文档", "order": index}
                for index, path in enumerate(roots)
            ],
        ],
        source_ref=profile.source.ref,
        parent_view_id=None,
        navigation=True,
        mode="collection",
    )
    graph: dict[str, object] = {
        "contract_version": "0.2.0",
        "graph_id": f"material-graph:material-wiki/{project_id}",
        "generated_at": generated,
        "active_view_id": root_view_id(project_id),
        "materials": [root, *category_views, *document_views, *pinned_materials, *body_materials, *relations],
    }
    assert_material_graph(graph)
    return graph
