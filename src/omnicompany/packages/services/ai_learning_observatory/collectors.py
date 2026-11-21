# [OMNI] origin=codex created_by=codex intent=read-only-cross-project-evidence-collection
"""Bounded, read-only evidence collection for selected projects."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root
from omnicompany.core.projects_registry import (
    _plan_catalogue,
    plan_governance,
    resolve_project_plans,
)


_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__",
    ".pytest_cache", "snapshots", "blobs", "archive", "_archive",
}
_RECENT_NAMES = ("review", "report", "accept", "verify", "test", "handoff", "receipt")


def collect_project_snapshot(
    project: dict[str, Any],
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect reference-level evidence without changing any source project."""
    observed_at = now or datetime.now(timezone.utc)
    project_id = str(project.get("project_id") or project.get("id") or "")
    registry = project.get("registry") if isinstance(project.get("registry"), dict) else project
    roots = _existing_paths(registry.get("roots") or [])
    index_path = _path_or_none(registry.get("index_path"))
    cfg = config["collection"]
    categories = {
        name: {
            "status": "excluded" if name in set(cfg.get("privacy_excluded_categories") or []) else "empty",
            "items": [], "errors": [],
        }
        for name in cfg["source_categories"]
    }

    def add(category: str, kind: str, source_ref: str, summary: str, data: dict[str, Any]) -> str:
        if categories[category]["status"] == "excluded":
            return ""
        payload = {
            "kind": kind,
            "source_ref": source_ref,
            "summary": _clip(summary, 600),
            "data": _jsonable(data),
        }
        digest = _digest(payload)
        item = {"id": f"ev-{digest[:16]}", "digest": digest, **payload}
        categories[category]["items"].append(item)
        categories[category]["status"] = "available"
        return item["id"]

    def fail(category: str, exc: Exception | str) -> None:
        if categories[category]["status"] == "excluded":
            return
        categories[category]["errors"].append(_clip(str(exc), 500))
        if not categories[category]["items"]:
            categories[category]["status"] = "unavailable"

    # Identity, direction, declared authority and freshness.
    identity = {
        key: registry.get(key)
        for key in (
            "id", "name", "desc", "group", "roots", "index_path", "index_ok",
            "index_error", "index_stale", "last_active", "activity_7d", "plan_count",
        )
    }
    add("identity_and_direction", "project_registry", f"project:{project_id}",
        f"Project registry identity for {project_id}", identity)
    add("authority_and_freshness", "project_registry_freshness", f"project:{project_id}",
        "Registry authority and freshness signals", {
            "index_ok": registry.get("index_ok"),
            "index_stale": registry.get("index_stale"),
            "last_active": registry.get("last_active"),
        })
    if index_path and index_path.is_file():
        try:
            text = index_path.read_text(encoding="utf-8", errors="replace")
            stat = index_path.stat()
            add("identity_and_direction", "project_index", str(index_path),
                "Project index exists; content retained by digest only", {
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "size": stat.st_size,
                    "modified_at": _mtime_iso(index_path),
                    "headings": [line.strip() for line in text.splitlines() if line.startswith("#")][:16],
                })
        except OSError as exc:
            fail("identity_and_direction", exc)

    # Plans are the main execution/output boundary, but not the only evidence source.
    plans: list[dict[str, Any]] = []
    businesses: dict[str, dict[str, Any]] = {}
    tasks: list[dict[str, Any]] = []
    try:
        plans = resolve_project_plans(
            project_id,
            registry.get("plan_categories") or [],
            _plan_catalogue(),
            plan_governance(),
        )
        plans.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("id") or "")), reverse=True)
        plans = plans[: int(cfg.get("max_plans_per_project") or 8)]
        for plan in plans:
            meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
            business_slug = str(
                meta.get("business_id") or meta.get("business") or meta.get("work_type")
                or project.get("work_type") or "project-work"
            ).strip()
            business_id = f"{project_id}/{business_slug}"
            businesses.setdefault(business_id, {
                "business_id": business_id,
                "project_id": project_id,
                "parent_business_id": meta.get("parent_business_id"),
                "label": business_slug,
                "identity_source": f"plan:{plan.get('id')}",
                "projection_only": True,
            })
            tasks.append({
                "task_id": str(plan.get("id") or ""),
                "project_id": project_id,
                "business_id": business_id,
                "source_ref": f"plan:{plan.get('id')}",
                "status": meta.get("status") or ("archived" if plan.get("archived") else "observed"),
                "acceptance_criteria": meta.get("exit_criteria") or [],
            })
            add("execution", "plan", f"plan:{plan.get('id')}",
                str(meta.get("title") or plan.get("topic") or plan.get("id")), {
                    "plan_id": plan.get("id"), "date": plan.get("date"),
                    "folder_path": plan.get("folder_path"), "file_count": plan.get("file_count"),
                    "authority": meta.get("authority"), "work_type": meta.get("work_type"),
                    "exit_criteria": meta.get("exit_criteria") or [],
                    "updated": meta.get("updated"),
                })
            if meta.get("exit_criteria"):
                add("actual_outcome", "declared_exit_criteria", f"plan:{plan.get('id')}",
                    "Declared outcome criteria; declaration is not completion proof", {
                        "plan_id": plan.get("id"), "exit_criteria": meta.get("exit_criteria"),
                    })
    except Exception as exc:  # noqa: BLE001 - every source reports its own failure.
        fail("execution", exc)

    plan_ids = {str(plan.get("id") or "") for plan in plans}

    # Structured current-session metadata. Never copy full chat bodies.
    session_rows: list[dict[str, Any]] = []
    session_path = omni_workspace_root() / "data" / "cc_sessions.json"
    try:
        raw = json.loads(session_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            source_rows = [(None, row) for row in raw]
        elif isinstance(raw, dict) and isinstance(raw.get("sessions"), list):
            source_rows = [(None, row) for row in raw.get("sessions") or []]
        elif isinstance(raw, dict):
            source_rows = list(raw.items())
        else:
            source_rows = []
        for record_key, row in source_rows:
            if not isinstance(row, dict) or not _session_matches(row, project_id, plan_ids, roots):
                continue
            compact = {
                key: row.get(key)
                for key in (
                    "id", "title", "provider", "model", "status", "active_plan", "active_project",
                    "cwd", "created_at", "updated_at", "started_at", "ended_at", "last_submit_at",
                    "last_token_budget", "event_count", "provider_session_id", "exit_reason",
                )
            }
            compact["id"] = compact.get("id") or record_key
            session_rows.append(compact)
        session_rows.sort(
            key=lambda row: str(row.get("updated_at") or row.get("last_submit_at") or row.get("started_at") or ""),
            reverse=True,
        )
        session_rows = session_rows[: int(cfg.get("max_sessions_per_project") or 20)]
        for row in session_rows:
            source_ref = f"session:{row.get('id')}"
            add("execution", "session_metadata", source_ref, "Project-linked session metadata", row)
            add("efficiency_and_cost", "session_usage_metadata", source_ref,
                "Available provider/model/token-budget metadata", {
                    key: row.get(key) for key in (
                        "provider", "model", "last_token_budget", "event_count", "started_at", "ended_at", "last_submit_at",
                    )
                })
    except (OSError, json.JSONDecodeError) as exc:
        fail("execution", exc)
        fail("efficiency_and_cost", exc)

    # Connect the existing ccusage projection.  Values remain attributed to
    # the authoritative cache file/session id and carry explicit freshness;
    # missing or stale usage never becomes zero.
    try:
        for row in _ccusage_project_sessions(
            project_id,
            session_rows,
            observed_at=observed_at,
            limit=int(cfg.get("max_ccusage_sessions_per_project") or 20),
        ):
            add(
                "efficiency_and_cost",
                "ccusage_session_usage",
                row.pop("source_ref"),
                "ccusage session token/cost projection with source freshness",
                row,
            )
    except Exception as exc:  # noqa: BLE001 - cache projection failure is isolated.
        fail("efficiency_and_cost", exc)

    # Native session files add occurrence/cwd evidence, still without copying content.
    try:
        from omnicompany.packages.services._diagnosis.project_audit.workers._sessions import (
            first_cwd,
            iter_session_files,
        )
        all_files = list(iter_session_files())
        provider_ids = {
            str(row.get(key) or "")
            for row in session_rows
            for key in ("provider_session_id", "id")
            if row.get(key)
        }
        linked_files = [
            item for item in all_files
            if any(token.lower() in Path(item).name.lower() for token in provider_ids)
        ]
        recent_files = sorted(
            all_files, key=lambda item: _safe_mtime(Path(item)), reverse=True,
        )[: int(cfg.get("max_native_session_files") or 80)]
        files = list(dict.fromkeys([*linked_files, *recent_files]))
        matched = 0
        for file_name in files:
            path = Path(file_name)
            cwd = first_cwd(file_name)
            linked = any(token.lower() in path.name.lower() for token in provider_ids)
            if not (linked or _path_matches(cwd, roots)):
                continue
            matched += 1
            add("execution", "native_session_ref", str(path),
                "Native session reference matched by cwd or project/plan id", {
                    "cwd": cwd, "modified_at": _mtime_iso(path), "size": _safe_size(path),
                })
            if matched >= int(cfg.get("max_sessions_per_project") or 20):
                break
    except Exception as exc:  # noqa: BLE001
        fail("execution", exc)

    # Decisions are semantic signals: retain the decision statement and authority metadata.
    try:
        from omnicompany.packages.domains.decisions.library import active_records
        decisions = []
        for row in active_records():
            if not isinstance(row, dict) or not _decision_matches(row, project_id, plan_ids):
                continue
            decisions.append(row)
        decisions.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        for row in decisions[: int(cfg.get("max_decisions_per_project") or 20)]:
            add("decision_basis", "decision", f"decision:{row.get('id')}",
                str(row.get("statement") or row.get("id")), {
                    "decision_id": row.get("id"), "status": row.get("status"),
                    "authority": row.get("authority"), "rationale": row.get("rationale"),
                    "project": row.get("project"), "track": row.get("track"),
                    "updated_at": row.get("updated_at"), "links": row.get("links") or {},
                })
    except Exception as exc:  # noqa: BLE001
        fail("decision_basis", exc)

    # Available manuals/skills/teams are recorded separately from proven consumption.
    # A later session/task link is required before the evaluator may claim they were used.
    authority_files = []
    for path in _named_authority_files(roots, limit=16):
        authority_files.append(path)
        add("decision_basis", "available_manual_or_skill", str(path),
            "Relevant authority file discovered; actual task consumption is not proven", {
                "name": path.name, "modified_at": _mtime_iso(path),
                "consumption_state": "discovered_not_proven",
            })
    try:
        from omnicompany.dashboard.controlplane.catalogue import list_teams
        for team in (list_teams().get("items") or []):
            file_path = str(team.get("file_path") or "")
            if not _path_matches(file_path, roots):
                continue
            add("decision_basis", "available_team", file_path,
                "Project-local Team discovered; actual task consumption is not proven", {
                    "team_id": team.get("id"), "package": team.get("package"),
                    "registered_via": team.get("registered_via"),
                    "modified_at": _mtime_iso(Path(file_path)),
                    "consumption_state": "discovered_not_proven",
                })
    except Exception as exc:  # noqa: BLE001
        fail("decision_basis", exc)

    # Git produces engineering-change and downstream signals for each distinct repo.
    seen_git: set[str] = set()
    for root in roots:
        try:
            git_root = _git(root, ["rev-parse", "--show-toplevel"], cfg).strip()
            if not git_root:
                continue
            relative_root = os.path.relpath(root, Path(git_root)).replace("\\", "/")
            pathspec = "." if relative_root == "." else relative_root
            git_key = f"{git_root.lower()}::{pathspec.lower()}"
            if git_key in seen_git:
                continue
            seen_git.add(git_key)
            status_lines = [
                line for line in _git(Path(git_root), ["status", "--short", "--", pathspec], cfg).splitlines()
                if line
            ]
            log_lines = _git(
                Path(git_root), ["log", "-5", "--format=%H%x09%cI%x09%s", "--", pathspec], cfg,
            ).splitlines()
            commits = []
            for line in log_lines:
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    commits.append({"sha": parts[0], "committed_at": parts[1], "subject": parts[2]})
            ref = f"git:{git_root}"
            add("engineering_change", "git_state", ref, "Repository change and recent commit state", {
                "root": git_root, "observed_path": str(root), "pathspec": pathspec,
                "dirty_path_count": len(status_lines), "recent_commits": commits,
            })
            add("downstream_evidence", "git_history", ref,
                "Recent commits are downstream artifacts, not proof of quality", {
                    "root": git_root, "observed_path": str(root), "pathspec": pathspec,
                    "recent_commits": commits,
                })
        except Exception as exc:  # noqa: BLE001
            fail("engineering_change", f"{root}: {exc}")

    # ReviewStage captures later verdicts and manual interventions.
    # 2026-08-20 审阅库融合: 读源换共享核心 review_store(画布 card.submission 投影,
    # 形状兼容旧 Material.to_dict); 内部治理件过滤沿用 reviewstage 权威判定。
    try:
        from omnicompany.packages.services._core import review_store
        from omnicompany.packages.services.boss_sight.reviewstage.store import (
            Material,
            is_internal_review_material,
        )
        max_reviews = int(cfg.get("max_reviews_per_project") or 20)
        materials = []
        for row in review_store.list_materials():
            if str(row.get("project") or "") != project_id:
                continue
            if is_internal_review_material(Material.from_dict(row)):
                continue
            materials.append(row)
            if len(materials) >= max_reviews:
                break
        for row in materials:
            compact = {
                key: row.get(key)
                for key in (
                    "id", "title", "status", "tier", "source_plan_id", "created_at", "updated_at",
                    "pushed_to_user", "pushed_reason", "view_count", "opened_count", "viewed_at",
                )
            }
            compact["history"] = [
                {
                    key: (_clip(str(entry.get(key) or ""), 300) if key == "reason" else entry.get(key))
                    for key in ("event", "from", "to", "by", "reason", "at")
                    if entry.get(key) is not None
                }
                for entry in (row.get("history") or [])[-12:]
                if isinstance(entry, dict)
            ]
            compact["comments"] = [_comment_projection(comment) for comment in (row.get("comments") or [])[-12:]]
            ref = f"review:{row.get('id')}"
            add("verification", "review_material", ref, str(row.get("title") or row.get("id")), compact)
            add("actual_outcome", "review_verdict", ref, "Human or review-stage outcome signal", {
                "status": row.get("status"), "updated_at": row.get("updated_at"),
                "source_plan_id": row.get("source_plan_id"),
            })
            intervention = _material_intervention(row)
            if intervention:
                add("human_intervention", "review_intervention", ref,
                    "Manual verdict, comment, or explicit patch signal", intervention)
    except Exception as exc:  # noqa: BLE001
        fail("verification", exc)
        fail("human_intervention", exc)

    # Bounded file metadata catches verification artifacts outside ReviewStage.
    recent_after = observed_at.timestamp() - int(cfg.get("recent_file_days") or 45) * 86400
    search_roots = list(roots)
    for plan in plans:
        folder = _path_or_none(plan.get("folder_path"))
        if folder:
            search_roots.append(folder)
    for path in _recent_named_files(search_roots, recent_after, int(cfg.get("max_recent_files_per_root") or 24)):
        add("verification", "verification_artifact", str(path), "Recent verification-like artifact", {
            **_verification_projection(
                path,
                max_bytes=int(cfg.get("max_structured_artifact_bytes") or 1048576),
            ),
        })

    # Empty is evidence too; downstream consumers must not mistake it for success.
    for category in categories.values():
        if category["items"] and category["errors"]:
            category["status"] = "conflict"
    if registry.get("index_stale") and categories["authority_and_freshness"]["status"] == "available":
        categories["authority_and_freshness"]["status"] = "stale"
    coverage = {
        status: sum(1 for row in categories.values() if row["status"] == status)
        for status in ("available", "empty", "unavailable", "stale", "conflict", "excluded")
    }
    evidence_basis = {
        name: [{"id": item["id"], "digest": item["digest"]} for item in row["items"]]
        for name, row in categories.items()
    }
    return {
        "schema_version": 1,
        "collector_version": "ai-learning-observatory-v1",
        "project": {
            "id": project_id,
            "name": project.get("name") or registry.get("name") or project_id,
            "group": project.get("group") or registry.get("group"),
            "work_type": project.get("work_type"),
        },
        "collected_at": observed_at.isoformat(),
        "config_ref": config.get("_config_path"),
        "sources": categories,
        "work_units": {
            "businesses": sorted(businesses.values(), key=lambda row: row["business_id"]),
            "tasks": tasks,
            "identity_note": "Business ids are a comparison projection derived from existing plan metadata; Task ids reuse plan ids.",
        },
        "coverage": {**coverage, "queried": len(categories)},
        "evidence_hash": _digest(evidence_basis),
    }


def _decision_matches(row: dict[str, Any], project_id: str, plan_ids: set[str]) -> bool:
    if str(row.get("project") or "").lower() == project_id.lower():
        return True
    track = row.get("track")
    if isinstance(track, dict) and str(track.get("id") or "") in plan_ids:
        return True
    tags = {str(tag).lower() for tag in row.get("tags") or []}
    return project_id.lower() in tags


def _session_matches(row: dict[str, Any], project_id: str, plan_ids: set[str], roots: list[Path]) -> bool:
    if str(row.get("active_project") or "").lower() == project_id.lower():
        return True
    active_plan = str(row.get("active_plan") or "")
    if active_plan in plan_ids or any(active_plan.startswith(pid + "/") for pid in plan_ids if pid):
        return True
    return _path_matches(str(row.get("cwd") or ""), roots)


def _material_intervention(row: dict[str, Any]) -> dict[str, Any] | None:
    history = [entry for entry in row.get("history") or [] if isinstance(entry, dict)]
    manual = [entry for entry in history if entry.get("event") in {"verdict", "comment", "extra_patched"}]
    comments = row.get("comments") or []
    if not manual and not comments and row.get("status") not in {"rejected", "blocked"}:
        return None
    return {
        "status": row.get("status"), "history": manual[-8:], "comment_count": len(comments),
        "pushed_reason": row.get("pushed_reason"),
        "view_count": row.get("view_count") if row.get("view_count") is not None else row.get("opened_count"),
        "view_count_status": (
            "observed"
            if row.get("view_count") is not None or row.get("opened_count") is not None
            else "unknown_not_zero"
        ),
    }


def _comment_projection(comment: Any) -> dict[str, Any]:
    if not isinstance(comment, dict):
        return {"invalid": True}
    content = str(comment.get("content") or "")
    return {
        key: comment.get(key)
        for key in ("id", "author", "created_at", "feedback_status", "target")
        if comment.get(key) is not None
    } | {
        "content_preview": _clip(content, 300),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_length": len(content),
    }


def _ccusage_project_sessions(
    project_id: str,
    session_rows: list[dict[str, Any]],
    *,
    observed_at: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """Read the newest existing ccusage cache without starting a new scan."""

    cache_root = omni_workspace_root() / "data" / "boss_sight" / "ccusage_cache"
    if not cache_root.is_dir():
        return []
    candidates = [
        path
        for path in cache_root.glob("*.json")
        if "__" in path.stem and _safe_size(path) and (_safe_size(path) or 0) <= 4_000_000
    ]
    candidates.sort(key=_safe_mtime, reverse=True)
    provider_ids = {
        str(row.get(key) or "").lower()
        for row in session_rows
        for key in ("provider_session_id", "id")
        if row.get(key)
    }
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, list):
            continue
        generated = payload.get("generated_at")
        try:
            generated_at = datetime.fromtimestamp(float(generated), tz=timezone.utc)
            freshness_hours = max(
                0.0, (observed_at - generated_at).total_seconds() / 3600
            )
            generated_text = generated_at.isoformat()
        except (TypeError, ValueError, OSError):
            freshness_hours = None
            generated_text = None
        matched: list[dict[str, Any]] = []
        for item in sessions:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("sid") or "")
            project_match = str(item.get("project") or "").lower() == project_id.lower()
            session_match = any(token and token in sid.lower() for token in provider_ids)
            if not (project_match or session_match):
                continue
            matched.append(
                {
                    "source_ref": f"{path.resolve()}#session={sid}",
                    "session_id": sid,
                    "project": item.get("project"),
                    "plan": item.get("plan"),
                    "agent": item.get("agent"),
                    "day": item.get("day"),
                    "input_tokens": item.get("inputTokens"),
                    "output_tokens": item.get("outputTokens"),
                    "cache_read_tokens": item.get("cacheReadTokens"),
                    "cache_creation_tokens": item.get("cacheCreationTokens"),
                    "total_tokens": item.get("totalTokens"),
                    "total_cost": item.get("totalCost"),
                    "models": item.get("models") or [],
                    "source_generated_at": generated_text,
                    "source_freshness_hours": (
                        round(freshness_hours, 2) if freshness_hours is not None else None
                    ),
                    "freshness_state": (
                        "fresh"
                        if freshness_hours is not None and freshness_hours <= 24
                        else "stale"
                        if freshness_hours is not None
                        else "unknown"
                    ),
                    "missing_is_zero": False,
                }
            )
        if matched:
            matched.sort(key=lambda row: str(row.get("day") or ""), reverse=True)
            return matched[: max(1, limit)]
    return []


_RESULT_KEYS = {
    "status",
    "result",
    "verdict",
    "passed",
    "success",
    "ok",
    "exit_code",
    "exitcode",
    "failures",
    "failed",
    "errors",
    "error_count",
    "passed_count",
    "total",
    "tests",
    "score",
    "duration_ms",
    "duration_seconds",
}
_EXPLICIT_STATUS_VALUES = {
    "pass",
    "passed",
    "success",
    "succeeded",
    "ok",
    "fail",
    "failed",
    "failure",
    "error",
    "accepted",
    "rejected",
    "blocked",
}


def _verification_projection(path: Path, *, max_bytes: int) -> dict[str, Any]:
    """Parse only bounded structured result fields; never infer from a filename."""

    size = _safe_size(path)
    projection: dict[str, Any] = {
        "name": path.name,
        "modified_at": _mtime_iso(path),
        "size": size,
        "structured_parse": "not_supported",
        "result_fields": {},
        "explicit_outcome": False,
        "filename_is_not_outcome": True,
    }
    if size is None or size > max_bytes:
        projection["structured_parse"] = "skipped_size_limit"
        return projection
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            fields = _extract_result_fields(value)
            projection["structured_parse"] = "parsed_json"
            projection["result_fields"] = fields
            projection["explicit_outcome"] = _has_explicit_outcome(fields)
        elif suffix == ".xml":
            root = ET.parse(path).getroot()
            attrs = {
                key: root.attrib.get(key)
                for key in ("tests", "failures", "errors", "skipped", "time")
                if root.attrib.get(key) is not None
            }
            projection["structured_parse"] = "parsed_xml_root"
            projection["result_fields"] = attrs
            projection["explicit_outcome"] = bool(
                {"tests", "failures", "errors"} & set(attrs)
            )
    except (OSError, json.JSONDecodeError, ET.ParseError, UnicodeError) as exc:
        projection["structured_parse"] = "parse_failed"
        projection["parse_error"] = _clip(str(exc), 240)
    return projection


def _extract_result_fields(value: Any, *, depth: int = 0, prefix: str = "$") -> dict[str, Any]:
    if depth > 4:
        return {}
    fields: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in list(value.items())[:200]:
            name = str(key)
            child_prefix = f"{prefix}.{name}"
            if name.lower() in _RESULT_KEYS and isinstance(
                child, (str, int, float, bool)
            ):
                fields[child_prefix] = _clip(str(child), 160) if isinstance(child, str) else child
                if len(fields) >= 40:
                    return fields
            if isinstance(child, (dict, list)):
                for child_key, child_value in _extract_result_fields(
                    child, depth=depth + 1, prefix=child_prefix
                ).items():
                    fields[child_key] = child_value
                    if len(fields) >= 40:
                        return fields
    elif isinstance(value, list):
        for index, child in enumerate(value[:20]):
            if isinstance(child, (dict, list)):
                fields.update(
                    _extract_result_fields(
                        child, depth=depth + 1, prefix=f"{prefix}[{index}]"
                    )
                )
                if len(fields) >= 40:
                    break
    return fields


def _has_explicit_outcome(fields: dict[str, Any]) -> bool:
    for path, value in fields.items():
        key = path.rsplit(".", 1)[-1].lower()
        if key in {"passed", "success", "ok", "exit_code", "exitcode", "failures", "errors"}:
            return True
        if key in {"status", "result", "verdict"} and str(value).lower() in _EXPLICIT_STATUS_VALUES:
            return True
    return False


def _recent_named_files(roots: Iterable[Path], recent_after: float, per_root: int) -> Iterable[Path]:
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        base = root if root.is_dir() else root.parent
        candidates: list[tuple[float, Path]] = []
        visited = 0
        for current, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if name.lower() not in _SKIP_DIRS]
            for name in files:
                visited += 1
                if visited > 8000:
                    break
                if not any(token in name.lower() for token in _RECENT_NAMES):
                    continue
                path = Path(current) / name
                modified = _safe_mtime(path)
                if modified >= recent_after:
                    candidates.append((modified, path))
            if visited > 8000:
                break
        for _, path in sorted(candidates, reverse=True)[:per_root]:
            key = str(path.resolve()).lower()
            if key not in seen:
                seen.add(key)
                yield path


def _named_authority_files(roots: Iterable[Path], limit: int) -> Iterable[Path]:
    found: list[Path] = []
    visited = 0
    for root in roots:
        if not root.exists():
            continue
        base = root if root.is_dir() else root.parent
        for current, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if name.lower() not in _SKIP_DIRS]
            for name in files:
                visited += 1
                upper = name.upper()
                if upper in {"SKILL.MD", "AGENTS.MD"} or "MANUAL" in upper or "GUIDE" in upper:
                    found.append(Path(current) / name)
                    if len(found) >= limit:
                        return found
                if visited >= 6000:
                    return found
    return found


def _git(root: Path, args: list[str], cfg: dict[str, Any]) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=int(cfg.get("command_timeout_seconds") or 8),
        check=False,
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"git exit {proc.returncode}")
    return proc.stdout


def _existing_paths(values: Iterable[Any]) -> list[Path]:
    paths = []
    for value in values:
        path = _path_or_none(value)
        if path and path.exists():
            paths.append(path)
    return paths


def _path_or_none(value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = omni_workspace_root() / path
    return path.resolve()


def _path_matches(value: str, roots: list[Path]) -> bool:
    if not value:
        return False
    candidate = os.path.normcase(os.path.abspath(value))
    for root in roots:
        root_text = os.path.normcase(os.path.abspath(str(root)))
        if candidate == root_text or candidate.startswith(root_text.rstrip(os.sep) + os.sep):
            return True
    return False


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _digest(value: Any) -> str:
    raw = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


__all__ = ["collect_project_snapshot"]
