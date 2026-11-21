"""Canonical project resolution shared by resident display and digest binding.

Project ids come exclusively from ``core.projects_registry``. Workspace folder
names and free-form model labels are evidence, never project identities.
"""
from __future__ import annotations

import os
from typing import Any

VOID_PROJECT_LABELS = {"", "无", "信息不足", "未归属", "未绑定", "未知项目"}


def normal_label(value: Any) -> str:
    return "".join(ch.casefold() for ch in str(value or "") if ch.isalnum())


def normal_path(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(value or "").strip())).replace("\\", "/")


def _identity_aliases(project: dict[str, Any]) -> set[str]:
    return {
        normal_label(project.get("id")),
        normal_label(project.get("name")),
        normal_label(project.get("short")),
    } - {""}


def _routing_aliases(project: dict[str, Any]) -> set[str]:
    return {
        normal_label(value) for value in project.get("plan_categories") or []
    } - {""}


def project_ids_referenced_in_text(
    text: Any,
    *,
    projects: list[dict[str, Any]] | None = None,
) -> set[str]:
    """Return every registered project explicitly referenced by user text.

    Identity aliases (id/name/short) and plan-routing aliases are both useful
    evidence. Returning a set is deliberate: callers must treat two or more
    matches as a multi-project conversation, never choose the first one.
    """

    if projects is None:
        from omnicompany.core.projects_registry import list_projects

        projects = list_projects()
    normalized_text = normal_label(text)
    if not normalized_text:
        return set()
    matches: set[str] = set()
    matched_routing_aliases: set[str] = set()
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        identity_aliases = _identity_aliases(project)
        routing_aliases = _routing_aliases(project)
        if project_id and any(
            len(alias) >= 2 and alias in normalized_text
            for alias in identity_aliases | routing_aliases
        ):
            matches.add(project_id)
        matched_routing_aliases.update(
            alias
            for alias in routing_aliases
            if len(alias) >= 2 and alias in normalized_text
        )

    # A routing label such as ``demogame`` may be registered on one plan family
    # while also being the prefix of several distinct project identities. Treat
    # the whole family as referenced so callers see ambiguity instead of binding
    # the one project that happened to own the category string.
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        if project_id and any(
            routing_alias in identity_alias
            for routing_alias in matched_routing_aliases
            for identity_alias in _identity_aliases(project)
        ):
            matches.add(project_id)
    return matches


def canonical_project_id(
    *,
    cwd: Any = "",
    label: Any = "",
    projects: list[dict[str, Any]] | None = None,
) -> str:
    """Return one registered id or empty when the evidence is ambiguous.

    ``cwd`` stays in the signature for callers compiled against the old
    resolver, but it is intentionally ignored. A workspace path describes
    where a runtime happened to execute; only an explicit/content label may
    establish conversation ownership. Filesystem routing that needs roots must
    use a separate path helper and must not write a session project binding.
    """
    if projects is None:
        from omnicompany.core.projects_registry import list_projects

        projects = list_projects()

    normalized_label = normal_label(label)
    if not normalized_label or normalized_label in {
        normal_label(value) for value in VOID_PROJECT_LABELS
    }:
        return ""

    identity_exact: set[str] = set()
    routing_exact: set[str] = set()
    contextual: set[str] = set()
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            continue
        identity_aliases = _identity_aliases(project)
        routing_aliases = _routing_aliases(project)
        if normalized_label in identity_aliases:
            identity_exact.add(project_id)
        if normalized_label in routing_aliases:
            routing_exact.add(project_id)
        if any(
            len(alias) >= 3
            and (alias in normalized_label or normalized_label in alias)
            for alias in identity_aliases
        ):
            contextual.add(project_id)
    if len(identity_exact) == 1:
        return next(iter(identity_exact))
    if identity_exact:
        return ""
    candidates = routing_exact | contextual
    if len(candidates) == 1:
        return next(iter(candidates))
    return ""


__all__ = [
    "VOID_PROJECT_LABELS",
    "canonical_project_id",
    "normal_label",
    "normal_path",
    "project_ids_referenced_in_text",
]
