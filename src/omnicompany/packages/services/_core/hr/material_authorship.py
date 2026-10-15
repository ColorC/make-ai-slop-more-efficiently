# [OMNI] material_id="material:core.hr.material_authorship.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="按 Material ID/revision 投影内容作者、执行者、维护责任与互评证据，角色不混算"
# [OMNI] tags=hr,material,authorship,peer-review
"""Project Material identity into separate content, execution, maintenance and review roles."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

import yaml

from .blame import blame
from .schema import HrEvent


class MaterialNotFoundError(LookupError):
    pass


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    try:
        raw = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _wiki_author(path: Path) -> tuple[str, str, str]:
    material = _frontmatter(path).get("material_wiki")
    if not isinstance(material, dict):
        return "", "", ""
    revision = str(material.get("current_version") or material.get("head_version") or "")
    versions = material.get("versions")
    if isinstance(versions, list):
        for version in versions:
            if isinstance(version, dict) and str(version.get("version") or "") == revision:
                return revision, str(version.get("author_session") or ""), str(version.get("body_sha256") or "")
    return revision, "", ""


def _file_revision(path: Path) -> str:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
    return f"sha256:{digest}"


def _read_actor_reviews(path: Path, subject_sessions: set[str]) -> list[dict[str, Any]]:
    if not path.is_file() or not subject_sessions:
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or str(row.get("subject_session") or "") not in subject_sessions:
                continue
            rows.append({
                "review_id": str(row.get("id") or ""),
                "reviewer_provider": str(row.get("reviewer_provider") or ""),
                "reviewer_model": str(row.get("reviewer_model") or ""),
                "reviewer_session": str(row.get("reviewer_session") or ""),
                "severity": str(row.get("severity") or ""),
                "recorded_at": str(row.get("recorded_at") or ""),
                "evidence": list(row.get("evidence") or []),
                "issues": list(row.get("issues") or []),
            })
    return rows


def get_material_authorship(
    material_id: str,
    *,
    events: Iterable[HrEvent],
    index: Any | None = None,
    project_root: Path | None = None,
    peer_review_path: Path | None = None,
) -> dict[str, Any]:
    """Return role-separated authorship without inventing absent ownership."""
    if index is None:
        from omnicompany.packages.services._core.registry import get_material_id_index

        index = get_material_id_index()
    entry = index.lookup(material_id)
    if entry is None:
        raise MaterialNotFoundError(material_id)

    if project_root is None:
        from omnicompany.core.config import omni_workspace_root

        project_root = omni_workspace_root()
    project_root = Path(project_root)
    raw_path = Path(entry.file_path)
    path = raw_path if raw_path.is_absolute() else project_root / raw_path
    path = path.resolve(strict=False)

    revision_ref = ""
    author_session = ""
    body_sha256 = ""
    if str(getattr(entry, "kind", "")) == "material_wiki":
        revision_ref, author_session, body_sha256 = _wiki_author(path)
    if not revision_ref:
        revision_ref = _file_revision(path)

    attribution = blame(str(path), events=events)
    execution = {
        "actor_id": attribution.actor_id if attribution.tier == "direct" else "",
        "provider": attribution.provider if attribution.tier == "direct" else "",
        "model": attribution.model if attribution.tier == "direct" else "",
        "session_id": attribution.session_id if attribution.tier == "direct" else "",
        "tier": attribution.tier,
        "evidence_refs": attribution.evidence_refs,
    }
    subject_sessions = {value for value in (author_session, execution["session_id"]) if value}
    if "_" in author_session:
        subject_sessions.add(author_session.split("_", 1)[1])
    review_path = peer_review_path or project_root / "data" / "llm" / "agent_control" / "peer_reviews.jsonl"

    return {
        "material_id": material_id,
        "revision_ref": revision_ref,
        "file_path": str(path),
        "kind": str(getattr(entry, "kind", "")),
        "content_author": {
            "session_ref": author_session,
            "source": "material_wiki.current_version.author_session" if author_session else "unknown",
            "body_sha256": body_sha256,
        },
        "execution_actor": execution,
        "maintenance_owner": {
            "actor_id": "",
            "source": "unknown",
        },
        "declared_material_agent": str(getattr(entry, "agent", "") or ""),
        "actor_reviews": _read_actor_reviews(Path(review_path), subject_sessions),
    }


def material_authorship_coverage(
    *,
    events: Iterable[HrEvent],
    index: Any | None = None,
    project_root: Path | None = None,
    peer_review_path: Path | None = None,
) -> dict[str, Any]:
    """Measure each responsibility layer separately across the Material registry."""
    if index is None:
        from omnicompany.packages.services._core.registry import get_material_id_index

        index = get_material_id_index()
    if project_root is None:
        from omnicompany.core.config import omni_workspace_root

        project_root = omni_workspace_root()
    project_root = Path(project_root)

    latest_by_target: dict[str, HrEvent] = {}
    for event in events:
        if event.kind != "write" or event.verdict != "applied" or not event.target:
            continue
        normalized = os.path.normcase(os.path.normpath(os.path.abspath(event.target)))
        previous = latest_by_target.get(normalized)
        if previous is None or event.ts >= previous.ts:
            latest_by_target[normalized] = event

    review_path = peer_review_path or project_root / "data" / "llm" / "agent_control" / "peer_reviews.jsonl"
    reviewed_sessions: set[str] = set()
    if Path(review_path).is_file():
        with Path(review_path).open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("subject_session"):
                    reviewed_sessions.add(str(row["subject_session"]))

    total = content = execution = maintenance = reviewed = missing_files = 0
    by_kind: dict[str, dict[str, int]] = {}
    for entry in index.list_all():
        total += 1
        kind = str(getattr(entry, "kind", "") or "unknown")
        bucket = by_kind.setdefault(kind, {"total": 0, "content_author": 0, "execution_actor": 0})
        bucket["total"] += 1
        raw_path = Path(entry.file_path)
        path = raw_path if raw_path.is_absolute() else project_root / raw_path
        path = path.resolve(strict=False)
        if not path.is_file():
            missing_files += 1

        author_session = ""
        if kind == "material_wiki":
            _, author_session, _ = _wiki_author(path)
        if author_session:
            content += 1
            bucket["content_author"] += 1

        normalized = os.path.normcase(os.path.normpath(os.path.abspath(str(path))))
        actor = latest_by_target.get(normalized)
        if actor is not None:
            execution += 1
            bucket["execution_actor"] += 1

        subject_candidates = {author_session, actor.session_id if actor is not None else ""}
        if "_" in author_session:
            subject_candidates.add(author_session.split("_", 1)[1])
        if any(value in reviewed_sessions for value in subject_candidates if value):
            reviewed += 1

    def ratio(value: int) -> float:
        return value / total if total else 1.0

    return {
        "materials_total": total,
        "content_author_known": content,
        "content_author_coverage": ratio(content),
        "execution_actor_known": execution,
        "execution_actor_coverage": ratio(execution),
        "maintenance_owner_known": maintenance,
        "maintenance_owner_coverage": ratio(maintenance),
        "actor_review_linked": reviewed,
        "actor_review_coverage": ratio(reviewed),
        "missing_files": missing_files,
        "by_kind": by_kind,
    }


__all__ = ["MaterialNotFoundError", "get_material_authorship", "material_authorship_coverage"]
