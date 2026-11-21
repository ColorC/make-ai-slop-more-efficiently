# [OMNI] origin=codex domain=cli/commands ts=2026-05-17T00:00:00Z type=router status=draft
# [OMNI] summary="omni context resolve - resolve plan/path/kind/topic into progressive context file paths"
# [OMNI] why="OmniChat and external workers need omnicompany-owned context selection instead of Codex/Claude specific rule files"
# [OMNI] tags=cli,context,plan,standards,templates,dogfood
# [OMNI] material_id="material:cli.commands.progressive_context_resolver.py"
"""Resolve progressive context paths from plan/path/kind/topic inputs."""

from __future__ import annotations

import datetime as _dt
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import click

from omnicompany.packages.services._core.identity import current_session_meta


_CONTEXT_BINDINGS_RELPATH = "docs/standards/_meta/context-bindings.yaml"
_STANDARDS_INDEX_RELPATH = "docs/standards/_meta/standards-index.yaml"


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[4]


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _normalize_yaml_value(value: Any) -> Any:
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalize_yaml_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_value(v) for v in value]
    return value


def _read_frontmatter(md_path: Path) -> dict[str, Any]:
    if not md_path.is_file():
        return {}
    text = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n([\s\S]*?)\n---\s*", text)
    if not m:
        return {}
    try:
        import yaml

        data = yaml.safe_load(m.group(1))
    except Exception:
        return {}
    return _normalize_yaml_value(data) if isinstance(data, dict) else {}


def _plan_root(root: Path) -> Path:
    return root / "docs" / "plans"


def _resolve_current_plan_id() -> str | None:
    try:
        meta = current_session_meta()
    except Exception:
        return None
    plan_id = meta.get("active_plan")
    return str(plan_id) if plan_id else None


def _resolve_plan_id(plan: str | None) -> str | None:
    if not plan or plan == "current":
        return _resolve_current_plan_id()
    return plan


def _as_repo_path(path: str | Path, root: Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix().lstrip("./")


def _candidate_file(path: str, root: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else root / path


@lru_cache(maxsize=512)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    pat = pattern.replace("\\", "/")
    parts: list[str] = []
    i = 0
    while i < len(pat):
        if pat[i : i + 3] == "**/":
            parts.append(r"(?:.*/)?")
            i += 3
        elif pat[i : i + 2] == "**":
            parts.append(r".*")
            i += 2
        elif pat[i] == "*":
            parts.append(r"[^/]*")
            i += 1
        elif pat[i] == "?":
            parts.append(r"[^/]")
            i += 1
        else:
            parts.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def _match_glob(path: str, pattern: str) -> bool:
    return bool(_glob_to_regex(pattern).match(path.replace("\\", "/")))


def _path_exists(path: str, root: Path) -> bool:
    return _candidate_file(path, root).exists()


def _normalize_standard_path(path: str) -> str:
    p = path.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    elif p.startswith("/"):
        p = p.lstrip("/")
    if p.startswith("standards/"):
        return "docs/" + p
    return p


def _append_context(
    contexts: list[dict[str, Any]],
    seen: set[str],
    missing: list[dict[str, str]],
    *,
    root: Path,
    path: str,
    category: str,
    source: str,
    reason: str,
) -> None:
    norm = _normalize_standard_path(path)
    if norm in seen:
        return
    if not _path_exists(norm, root):
        missing.append({"path": norm, "source": source, "reason": reason})
        return
    seen.add(norm)
    contexts.append(
        {
            "path": norm,
            "category": category,
            "source": source,
            "reason": reason,
        }
    )


def _infer_kind_from_index(path: str, index: dict[str, Any]) -> str | None:
    for item in index.get("kind_inference") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        for pat in item.get("match") or []:
            if _match_glob(path, str(pat)):
                return kind or None
    return None


def _standards_for_path(path: str, kind: str | None, index: dict[str, Any]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for item in index.get("standards") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "")
        file_path = str(item.get("file") or "")
        applies_to = [str(x) for x in item.get("applies_to") or []]
        path_match = [str(x) for x in item.get("path_match") or []]
        kind_ok = not kind or not applies_to or kind in applies_to
        path_ok = any(_match_glob(path, pat) for pat in path_match)
        if kind_ok and path_ok and file_path:
            hits.append({"id": sid, "file": file_path})
    return hits


def _topic_matches(topic: str, keywords: list[Any]) -> list[str]:
    if not topic or not keywords:
        return []
    low = topic.lower()
    hits: list[str] = []
    for kw in keywords:
        text = str(kw).lower()
        if text and text in low:
            hits.append(str(kw))
    return hits


def _profile_match_reasons(
    profile: dict[str, Any],
    *,
    project: str | None,
    paths: list[str],
    kinds: set[str],
    topic: str,
) -> list[str]:
    applies = profile.get("applies") or {}
    if not isinstance(applies, dict):
        applies = {}

    reasons: list[str] = []
    if applies.get("always"):
        reasons.append("always")

    projects = [str(x) for x in applies.get("projects") or []]
    if projects and project and (project in projects or "*" in projects):
        reasons.append(f"project:{project}")

    profile_kinds = {str(x) for x in applies.get("kinds") or []}
    kind_hits = sorted(kinds & profile_kinds)
    if kind_hits:
        reasons.append("kind:" + ",".join(kind_hits))

    path_patterns = [str(x) for x in applies.get("path_match") or []]
    for path in paths:
        if any(_match_glob(path, pat) for pat in path_patterns):
            reasons.append(f"path:{path}")
            break

    kw_hits = _topic_matches(topic, list(applies.get("trigger_keywords") or []))
    if kw_hits:
        reasons.append("topic:" + ",".join(kw_hits[:5]))

    return reasons


def _include_profile_contexts(
    contexts: list[dict[str, Any]],
    seen: set[str],
    missing: list[dict[str, str]],
    *,
    root: Path,
    profile: dict[str, Any],
    reasons: list[str],
) -> None:
    include = profile.get("include") or {}
    if not isinstance(include, dict):
        return
    profile_id = str(profile.get("id") or "profile")
    reason = ";".join(reasons)
    for category, values in include.items():
        if not isinstance(values, list):
            continue
        for value in values:
            _append_context(
                contexts,
                seen,
                missing,
                root=root,
                path=str(value),
                category=str(category),
                source=profile_id,
                reason=reason,
            )


def resolve_context(
    *,
    root: Path,
    plan: str | None,
    paths: list[str],
    explicit_kinds: list[str],
    topic: str,
) -> dict[str, Any]:
    plan_id = _resolve_plan_id(plan)
    plan_dir = _plan_root(root) / plan_id if plan_id else None
    plan_md = plan_dir / "plan.md" if plan_dir else None
    brief_md = plan_dir / "brief.md" if plan_dir else None
    plan_meta = _read_frontmatter(plan_md) if plan_md else {}
    project = str(plan_meta.get("project") or "") or None

    repo_paths = [_as_repo_path(p, root) for p in paths]
    standards_index = _load_yaml(root / _STANDARDS_INDEX_RELPATH)
    context_index = _load_yaml(root / _CONTEXT_BINDINGS_RELPATH)

    inferred_kinds: dict[str, str | None] = {
        p: _infer_kind_from_index(p, standards_index) for p in repo_paths
    }
    kinds: set[str] = {k for k in explicit_kinds if k}
    kinds.update(k for k in inferred_kinds.values() if k)

    contexts: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    seen: set[str] = set()

    if plan_id and plan_md:
        _append_context(
            contexts,
            seen,
            missing,
            root=root,
            path=_as_repo_path(plan_md, root),
            category="plan",
            source="active_plan",
            reason=plan_id,
        )
    if plan_id and brief_md and brief_md.is_file():
        _append_context(
            contexts,
            seen,
            missing,
            root=root,
            path=_as_repo_path(brief_md, root),
            category="plan",
            source="active_plan",
            reason=plan_id,
        )

    for key in ("standards", "applicable_standards"):
        values = plan_meta.get(key) or []
        if isinstance(values, list):
            for value in values:
                _append_context(
                    contexts,
                    seen,
                    missing,
                    root=root,
                    path=str(value),
                    category=key,
                    source="active_plan_frontmatter",
                    reason=plan_id or "no-plan",
                )

    if project:
        _append_context(
            contexts,
            seen,
            missing,
            root=root,
            path=f"docs/plans/{project}/project.md",
            category="project",
            source="project_convention",
            reason=f"project:{project}",
        )

    for path in repo_paths:
        kind = next(iter(kinds), None) if not inferred_kinds.get(path) else inferred_kinds[path]
        for hit in _standards_for_path(path, kind, standards_index):
            _append_context(
                contexts,
                seen,
                missing,
                root=root,
                path=hit["file"],
                category="standards_index",
                source=hit["id"],
                reason=f"path:{path};kind:{kind or '-'}",
            )

    profiles = context_index.get("profiles") or []
    if isinstance(profiles, list):
        profiles_sorted = sorted(
            [p for p in profiles if isinstance(p, dict)],
            key=lambda p: int(p.get("priority") or 0),
            reverse=True,
        )
        for profile in profiles_sorted:
            reasons = _profile_match_reasons(
                profile,
                project=project,
                paths=repo_paths,
                kinds=kinds,
                topic=topic,
            )
            if reasons:
                _include_profile_contexts(
                    contexts,
                    seen,
                    missing,
                    root=root,
                    profile=profile,
                    reasons=reasons,
                )

    return {
        "plan_id": plan_id,
        "project": project,
        "paths": repo_paths,
        "explicit_kinds": explicit_kinds,
        "inferred_kinds": inferred_kinds,
        "topic": topic,
        "contexts": contexts,
        "total": len(contexts),
        "missing": missing,
        "missing_total": len(missing),
    }


@click.group("context")
def cmd_context() -> None:
    """Resolve distributed progressive context for OmniChat and workers."""


@cmd_context.command("resolve")
@click.option(
    "--plan",
    "plan_id",
    default="current",
    show_default=True,
    help="Plan id to use, or 'current'.",
)
@click.option(
    "--path",
    "paths",
    multiple=True,
    help="Target path involved in this turn. May be repeated.",
)
@click.option(
    "--kind",
    "kinds",
    multiple=True,
    help="Artifact kind hint such as material, worker, plan_md, standard_md.",
)
@click.option("--topic", default="", help="Natural-language topic for trigger matching.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--paths-only",
    is_flag=True,
    help="Emit only resolved existing context paths, one per line.",
)
def cmd_context_resolve(
    plan_id: str,
    paths: tuple[str, ...],
    kinds: tuple[str, ...],
    topic: str,
    as_json: bool,
    paths_only: bool,
) -> None:
    """Resolve applicable context paths from plan/path/kind/topic inputs."""
    root = _project_root()
    result = resolve_context(
        root=root,
        plan=plan_id,
        paths=list(paths),
        explicit_kinds=list(kinds),
        topic=topic,
    )
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if paths_only:
        for item in result["contexts"]:
            click.echo(item["path"])
        return

    click.echo(f"plan_id: {result['plan_id'] or '-'}")
    click.echo(f"project: {result['project'] or '-'}")
    click.echo(f"contexts: {result['total']}")
    for item in result["contexts"]:
        click.echo(
            f"- {item['path']}  [{item['category']}; {item['source']}; {item['reason']}]"
        )
    if result["missing_total"]:
        click.echo(f"missing/skipped: {result['missing_total']}")


@cmd_context.command("project")
@click.argument("query")
@click.option("--group", default=None, help="只在某分组里找")
@click.option("--limit", type=click.IntRange(min=1, max=50), default=8, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="输出机器可读 JSON")
def cmd_context_project(query: str, group: str | None, limit: int, as_json: bool) -> None:
    """上下文冷启动的项目别名查找；等价于 omni project find。"""
    from omnicompany.core.projects_registry import search_projects

    matches = search_projects(query, limit=limit, group=group)
    payload = {"query": query, "group": group, "matches": matches, "total": len(matches)}
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not matches:
        click.echo(f"(没有找到项目: {query})")
        return
    for item in matches:
        root = (item.get("roots") or [""])[0]
        click.echo(f"{item['id']}  {item['name']}  [{root}]" if root else f"{item['id']}  {item['name']}")


__all__ = ["cmd_context", "cmd_context_project", "resolve_context"]
