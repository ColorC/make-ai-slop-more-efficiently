# [OMNI] origin=codex domain=omnicompany/core ts=2026-10-04 type=registry status=active
"""Deterministic skill routing across Claude, Codex and shared skill roots.

The same skill is often exported into several runtime directories. This module
does not load skill bodies or decide task policy. It returns one canonical path
plus the locations that would otherwise compete, so callers can load one source
and keep project execution location in view.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root
from omnicompany.core.projects_registry import list_projects
from omnicompany.packages.domains.project_atlas._paths import SKILLS_ROOT


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("_", "-").split())


def _codex_skills_root() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser() / "skills"
    current = Path.home() / ".codex-2"
    return current / "skills" if current.is_dir() else Path.home() / ".codex" / "skills"


def skill_roots() -> tuple[tuple[str, Path, str], ...]:
    """Return roots in routing precedence order: canonical, project, shared."""

    workspace = omni_workspace_root()
    roots = [
        ("canonical", SKILLS_ROOT, "canonical"),
        ("workspace-agents", workspace / ".agents" / "skills", "project"),
        ("workspace-claude", workspace / ".claude" / "skills", "project"),
        ("agents", Path.home() / ".agents" / "skills", "shared"),
        ("codex", _codex_skills_root(), "runtime"),
        ("claude", Path.home() / ".claude" / "skills", "runtime"),
        ("codex-legacy", Path.home() / ".codex" / "skills", "legacy"),
    ]
    seen: set[str] = set()
    result: list[tuple[str, Path, str]] = []
    for label, root, scope in roots:
        key = str(root.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append((label, root, scope))
    return tuple(result)


def _iter_skill_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    return (
        path
        for path in sorted(root.rglob("SKILL.md"))
        if "archived-skills" not in {part.casefold() for part in path.parts}
    )


def _description(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:80]
    except OSError:
        return ""
    index = next((i for i, line in enumerate(lines) if line.startswith("description:")), None)
    if index is None:
        return ""
    value = lines[index].split(":", 1)[1].strip()
    if value in {"", ">", ">-", ">+", "|", "|-", "|+"}:
        parts: list[str] = []
        for line in lines[index + 1:]:
            if line and not line[0].isspace():
                break
            if line.strip():
                parts.append(line.strip())
        value = " ".join(parts)
    return " ".join(value.strip().strip("'\"").split())[:320]


def _project_for_cwd(cwd: str | Path | None) -> dict[str, str] | None:
    if cwd is None:
        return None
    try:
        current = Path(cwd).resolve()
    except OSError:
        return None
    candidates: list[tuple[int, dict[str, str]]] = []
    for item in list_projects():
        for raw_root in item.get("roots") or []:
            try:
                root = Path(str(raw_root)).resolve()
                current.relative_to(root)
            except (OSError, ValueError):
                continue
            candidates.append((len(str(root)), {
                "id": str(item.get("id") or ""),
                "root": str(root),
                "index_path": str(item.get("index_path") or ""),
            }))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _collect() -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for label, root, scope in skill_roots():
        for path in _iter_skill_files(root):
            name = path.parent.name
            key = _norm(name)
            if not key:
                continue
            grouped.setdefault(key, []).append({
                "name": name,
                "path": str(path.resolve()),
                "root": label,
                "scope": scope,
                "description": _description(path),
            })
    return grouped


def _content_variants(sources: list[dict[str, str]]) -> int:
    """Count distinct bodies for diagnostics; never gates a route."""
    bodies: list[bytes] = []
    for source in sources:
        try:
            body = Path(source["path"]).read_bytes()
        except OSError:
            continue
        if body not in bodies:
            bodies.append(body)
    return len(bodies)


_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s`\"'<>]+")


def _stale_path_refs(path: str) -> int:
    """Count missing absolute paths in a skill body for routing diagnostics.

    The count is deliberately advisory: it never blocks a route and it never
    rewrites a path whose owner is unclear. Project index data remains the
    authority for the execution location.
    """
    try:
        body = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    count = 0
    seen: set[str] = set()
    for raw in _ABSOLUTE_PATH_RE.findall(body):
        value = raw.rstrip(".,;:)]}，。；：、")
        # References such as ``file.py:42`` are still useful diagnostics, but
        # the line suffix should not make an otherwise existing file look stale.
        value = re.sub(r":\d+(?:-\d+)?$", "", value)
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        try:
            if not Path(value).exists():
                count += 1
        except (OSError, ValueError):
            count += 1
    return count


def resolve_skills(query: str, *, cwd: str | Path | None = None, limit: int = 8) -> list[dict[str, Any]]:
    """Resolve a skill name to one canonical load path and compact route data."""

    needle = _norm(query)
    if not needle:
        return []
    grouped = _collect()
    project = _project_for_cwd(cwd or Path.cwd())
    rows: list[dict[str, Any]] = []
    for key, sources in grouped.items():
        descriptions = " ".join(source["description"] for source in sources if source["description"])
        score = 0
        if key == needle:
            score = 100
        elif needle in key:
            score = 70
        elif needle in _norm(descriptions):
            score = 25
        if not score:
            continue
        canonical = next((source for source in sources if source["root"] == "canonical"), None)
        selected = canonical
        if selected is None and project:
            selected = next((source for source in sources if source["scope"] == "project"), None)
        if selected is None:
            selected = next((source for source in sources if source["root"] == "agents"), None)
        if selected is None:
            selected = sources[0]
        variants = _content_variants(sources)
        warnings: list[str] = []
        if variants > 1:
            warnings.append("content_conflict: multiple skill bodies; load only canonical_path")
        stale_refs = _stale_path_refs(selected["path"])
        if stale_refs:
            warnings.append(
                f"stale_absolute_paths:{stale_refs}; use project index for execution location"
            )
        rows.append({
            "name": selected["name"],
            "score": score,
            "canonical_path": selected["path"],
            "selected_from": selected["root"],
            "scope": selected["scope"],
            "duplicate_count": len(sources),
            "content_variants": variants,
            "stale_path_refs": stale_refs,
            "warnings": warnings,
            "runtime_paths": [source["path"] for source in sources],
            "project": project,
        })
    rows.sort(key=lambda row: (-int(row["score"]), str(row["name"]).casefold()))
    if len(rows) > 1 and int(rows[0]["score"]) >= 100:
        rows = rows[:1]
    return rows[: max(1, min(int(limit), 50))]


__all__ = ["resolve_skills", "skill_roots"]
