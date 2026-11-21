# [OMNI] origin=codex domain=core/plans ts=2026-06-19 type=infra status=active
# [OMNI] material_id="material:core.plans_catalogue.scanner.py"
"""Pure docs/plans catalogue scanning.

This module intentionally has no dashboard/FastAPI dependency. Dashboard routes,
governance commands, and core registry helpers can share the same plan catalogue
source without pulling optional web dependencies into the core install.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\](.+)$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_plan_frontmatter(plan_md: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a plan/project markdown file."""
    if not plan_md.is_file():
        return {}
    try:
        text = plan_md.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        import yaml

        data = yaml.safe_load(m.group(1)) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def find_project_for_plan(plan_id: str) -> tuple[str, Path] | None:
    """Infer the project directory for a plan id, returning (project_rel, project.md)."""
    parts = plan_id.split("/")
    if len(parts) < 3:
        return None
    project_rel = "/".join(parts[:-1])
    project_dir = _plans_root() / project_rel
    project_md = project_dir / "project.md"
    if not project_md.is_file():
        return None
    return (project_rel, project_md)


def parse_project_meta(plan_id: str) -> dict[str, Any]:
    """Parse frontmatter from the project.md that owns a plan id, when present."""
    found = find_project_for_plan(plan_id)
    if not found:
        return {}
    _, project_md = found
    return parse_plan_frontmatter(project_md)


def _docs_root() -> Path:
    return _project_root() / "docs"


def _plans_root() -> Path:
    return _docs_root() / "plans"


def _project_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root()


def _is_in_archive(rel_parts: tuple[str, ...]) -> bool:
    return any(p == "_archive" for p in rel_parts)


def _walk_plan_dirs(root: Path) -> list[Path]:
    """Find every `[YYYY-MM-DD]TOPIC` dir under root, skipping _archive subtrees."""
    found: list[Path] = []
    if not root.is_dir():
        return found

    def walk(d: Path) -> None:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if not e.is_dir():
                continue
            if e.name == "_archive":
                continue
            if DATE_RE.match(e.name):
                found.append(e)
                continue
            walk(e)

    walk(root)
    return found


@lru_cache(maxsize=1)
def _scan_cached(token: float) -> list[dict[str, Any]]:
    pr = _plans_root()
    items: list[dict[str, Any]] = []
    if not pr.is_dir():
        return items

    for entry in _walk_plan_dirs(pr):
        m = DATE_RE.match(entry.name)
        date = m.group(1) if m else None
        topic = m.group(2) if m else entry.name
        plan_id = str(entry.relative_to(pr)).replace(os.sep, "/")
        category = "/".join(entry.relative_to(pr).parts[:-1])
        files = []
        plan_md_exists = False
        try:
            for f in entry.rglob("*.md"):
                rel_parts = f.relative_to(entry).parts
                if _is_in_archive(rel_parts):
                    continue
                rel = "/".join(rel_parts)
                files.append(rel)
                if rel == "plan.md":
                    plan_md_exists = True
        except OSError:
            pass
        files.sort()
        meta = parse_plan_frontmatter(entry / "plan.md") if plan_md_exists else {}
        items.append(
            {
                "id": plan_id,
                "topic": topic,
                "date": date,
                "category": category,
                "folder_path": str(entry.relative_to(_project_root())).replace(os.sep, "/"),
                "files": files,
                "file_count": len(files),
                "has_plan_md": plan_md_exists,
                "meta": meta,
            }
        )

    items.sort(key=lambda x: (x.get("date") or "", x.get("topic") or ""), reverse=True)

    archive = pr / "_archive"
    if archive.is_dir():
        for entry in archive.iterdir():
            if not entry.is_dir():
                continue
            m = DATE_RE.match(entry.name)
            date = m.group(1) if m else None
            topic = m.group(2) if m else entry.name
            try:
                file_count = sum(1 for _ in entry.rglob("*.md"))
            except OSError:
                file_count = 0
            items.append(
                {
                    "id": f"_archive/{entry.name}",
                    "topic": topic,
                    "date": date,
                    "category": "_archive",
                    "folder_path": str(entry.relative_to(_project_root())).replace(os.sep, "/"),
                    "files": [],
                    "file_count": file_count,
                    "has_plan_md": False,
                    "archived": True,
                }
            )
    return items


def _scan() -> list[dict[str, Any]]:
    pr = _plans_root()
    token = pr.stat().st_mtime if pr.exists() else 0.0
    return _scan_cached(token)
