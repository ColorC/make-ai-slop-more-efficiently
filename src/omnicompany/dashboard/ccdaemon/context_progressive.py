# [OMNI] origin=codex domain=dashboard/ccdaemon ts=2026-05-17T00:00:00Z type=infra status=draft
# [OMNI] summary="Progressive context resolver adapter for OmniChat session UI and injection events"
# [OMNI] why="OmniChat must surface omnicompany-owned context resolution visibly instead of hiding it in prompt prefixes"
# [OMNI] tags=dashboard,ccdaemon,context,plan,omnichat,dogfood
# [OMNI] material_id="material:dashboard.ccdaemon.progressive_context_adapter.py"
"""Progressive context adapter for ccdaemon.

This module intentionally returns UI-friendly data in addition to the raw
resolver result: absolute paths, dashboard targets, VS Code URIs, and a compact
summary. The resolver remains repo-owned (`omni context resolve` uses the same
pure function), while chat/dashboard can explain exactly what was injected.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


def repo_root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def _plan_meta(active_plan: str | None) -> dict[str, Any]:
    if not active_plan:
        return {}
    try:
        from omnicompany.dashboard.controlplane.plans import (
            _plans_root,
            parse_plan_frontmatter,
        )

        return parse_plan_frontmatter(_plans_root() / active_plan / "plan.md") or {}
    except Exception:
        return {}


def _topic_from(active_plan: str | None, plan_meta: dict[str, Any], topic: str | None) -> str:
    parts: list[str] = []
    if topic:
        parts.append(topic)
    if active_plan:
        parts.append(active_plan)
    for key in ("title", "work_type", "project", "phase", "status"):
        value = plan_meta.get(key)
        if value:
            parts.append(str(value))
    standards = plan_meta.get("standards") or plan_meta.get("applicable_standards") or []
    if isinstance(standards, list):
        parts.extend(str(v) for v in standards)
    return " ".join(parts)


def _dashboard_target(path: str, active_plan: str | None) -> dict[str, str] | None:
    normalized = path.replace("\\", "/")
    if active_plan and normalized == f"docs/plans/{active_plan}/plan.md":
        return {"type": "plan", "id": active_plan}
    if normalized.startswith("docs/plans/") and normalized.endswith("/plan.md"):
        plan_id = normalized.removeprefix("docs/plans/").removesuffix("/plan.md")
        return {"type": "plan", "id": plan_id}
    if normalized.startswith("docs/") and normalized.endswith(".md"):
        note_id = normalized.removeprefix("docs/").removesuffix(".md")
        return {"type": "note", "id": note_id}
    return None


def _vscode_uri(abs_path: Path) -> str:
    posix = abs_path.as_posix()
    return "vscode://file/" + quote(posix, safe="/:")


def _enrich_items(items: list[dict[str, Any]], *, root: Path, active_plan: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        path = str(item.get("path") or "")
        if not path:
            continue
        p = Path(path)
        abs_path = p if p.is_absolute() else root / path
        out.append(
            {
                **item,
                "abs_path": str(abs_path),
                "exists": abs_path.exists(),
                "dashboard_target": _dashboard_target(path, active_plan),
                "vscode_uri": _vscode_uri(abs_path),
            }
        )
    return out


def resolve_progressive_context(
    *,
    active_plan: str | None,
    cwd: str | None = None,
    paths: list[str] | None = None,
    topic: str | None = None,
    plan_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve and enrich the context bundle for a session or turn."""

    root = repo_root()
    meta = plan_meta if plan_meta is not None else _plan_meta(active_plan)
    target_paths = list(paths or [])
    if active_plan:
        target_paths.insert(0, f"docs/plans/{active_plan}/plan.md")
    if cwd:
        try:
            cwd_path = Path(cwd)
            if cwd_path.is_file():
                target_paths.append(str(cwd_path))
        except Exception:
            pass

    try:
        from omnicompany.cli.commands.context import resolve_context

        result = resolve_context(
            root=root,
            plan=active_plan,
            paths=target_paths,
            explicit_kinds=[],
            topic=_topic_from(active_plan, meta, topic),
        )
    except Exception as exc:
        result = {
            "plan_id": active_plan,
            "project": meta.get("project"),
            "paths": target_paths,
            "explicit_kinds": [],
            "inferred_kinds": {},
            "topic": topic or "",
            "contexts": [],
            "total": 0,
            "missing": [],
            "missing_total": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    result = dict(result)
    result["contexts"] = _enrich_items(list(result.get("contexts") or []), root=root, active_plan=active_plan)
    result["missing"] = _enrich_items(list(result.get("missing") or []), root=root, active_plan=active_plan)
    result["resolved_at"] = time.time()
    result["summary"] = (
        f"{result.get('total', 0)} contexts"
        + (f", {result.get('missing_total', 0)} missing" if result.get("missing_total") else "")
    )
    return result


def build_context_frame(
    *,
    session_id: str,
    active_plan: str | None,
    cwd: str | None,
    trigger: str,
    switched: bool = False,
    topic: str | None = None,
    plan_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bundle = resolve_progressive_context(
        active_plan=active_plan,
        cwd=cwd,
        topic=topic,
        plan_meta=plan_meta,
    )
    return {
        "kind": "context_resolved",
        "session_id": session_id,
        "trigger": trigger,
        "switched": switched,
        "plan_id": active_plan,
        "context": bundle,
        "summary": f"{trigger}: {bundle.get('summary')}",
    }


__all__ = [
    "build_context_frame",
    "repo_root",
    "resolve_progressive_context",
]
