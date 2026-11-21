# [OMNI] origin=codex created_by=codex intent=dynamic-observed-project-discovery
"""Dynamic multi-project portfolio discovery."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from omnicompany.core.projects_registry import enrich_projects


ProjectLoader = Callable[..., dict[str, Any]]


def discover_portfolio(
    config: dict[str, Any],
    *,
    projects: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    project_loader: ProjectLoader | None = None,
) -> dict[str, Any]:
    """Select a diverse, explainable active portfolio without mutating the registry."""
    portfolio_cfg = config["portfolio"]
    observed_at = now or datetime.now(timezone.utc)
    if projects is None:
        loader = project_loader or enrich_projects
        projects = list((loader(fresh=True) or {}).get("projects") or [])

    excluded = {str(x) for x in portfolio_cfg.get("exclude_projects") or []}
    preferred_rows = portfolio_cfg.get("preferred_projects") or []
    preferred = {
        str(row["id"]): {
            "order": index,
            "work_type": str(row.get("work_type") or "unspecified"),
        }
        for index, row in enumerate(preferred_rows)
        if isinstance(row, dict) and row.get("id")
    }
    recent_days = int(portfolio_cfg.get("recent_activity_days") or 14)
    repeated_days = int(portfolio_cfg.get("repeated_use_days") or 45)
    maximum = int(portfolio_cfg.get("maximum_projects") or 6)
    minimum = int(portfolio_cfg.get("minimum_projects") or 5)

    candidates: list[dict[str, Any]] = []
    for project in projects:
        project_id = str(project.get("id") or "")
        if not project_id or project_id in excluded:
            continue
        last_active = _parse_datetime(project.get("last_active"))
        age_days = ((observed_at - last_active).total_seconds() / 86400) if last_active else None
        active_days = sum(1 for value in (project.get("activity_7d") or []) if value)
        reasons: list[str] = []
        score = 0.0
        pref = preferred.get(project_id)
        if pref and (age_days is None or age_days <= repeated_days):
            score += 120 - pref["order"]
            reasons.append("configured_initial_service")
        elif pref:
            reasons.append("configured_initial_service_outside_activity_window")
        if project.get("pinned"):
            score += 80
            reasons.append("project_registry_pinned")
        if age_days is not None and age_days <= recent_days:
            score += max(0.0, 70.0 - age_days * 2)
            reasons.append(f"active_within_{recent_days}d")
        elif age_days is not None and age_days <= repeated_days:
            score += 20
            reasons.append(f"used_within_{repeated_days}d")
        if active_days:
            score += active_days * 8
            reasons.append(f"active_days_7d={active_days}")
        plan_count = int(project.get("plan_count") or 0)
        if plan_count:
            score += min(plan_count, 10)
            reasons.append(f"plan_count={plan_count}")
        if project.get("index_stale"):
            reasons.append("index_stale_but_activity_preserved")

        work_type = (pref or {}).get("work_type") or _infer_work_type(project)
        candidates.append({
            "project_id": project_id,
            "name": project.get("name") or project_id,
            "group": project.get("group") or "other",
            "work_type": work_type,
            "score": round(score, 2),
            "selection_reasons": reasons or ["registered_project"],
            "last_active": project.get("last_active"),
            "active_days_7d": active_days,
            "plan_count": plan_count,
            "index_stale": bool(project.get("index_stale")),
            "registry": project,
        })

    candidates.sort(
        key=lambda row: (
            -row["score"],
            preferred.get(row["project_id"], {}).get("order", 9999),
            row["project_id"],
        )
    )
    selected = candidates[:maximum]
    missing_preferred = [project_id for project_id in preferred if project_id not in {
        row["project_id"] for row in candidates
    }]
    work_types = sorted({row["work_type"] for row in selected})
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "config_ref": config.get("_config_path"),
        "selection_policy": {
            "minimum_projects": minimum,
            "maximum_projects": maximum,
            "recent_activity_days": recent_days,
            "repeated_use_days": repeated_days,
        },
        "selected": selected,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "work_types": work_types,
        "missing_preferred": missing_preferred,
        "gate": {
            "minimum_projects_met": len(selected) >= minimum,
            "work_type_diversity_met": len(work_types) >= 3,
            "ok": len(selected) >= minimum and len(work_types) >= 3 and not missing_preferred,
        },
    }


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _infer_work_type(project: dict[str, Any]) -> str:
    group = str(project.get("group") or "other")
    if group == "indie-game":
        return "game-product"
    if group == "demogame":
        return "game-production"
    if group == "omnicompany":
        return "agent-platform"
    return "general-product"


__all__ = ["discover_portfolio"]
