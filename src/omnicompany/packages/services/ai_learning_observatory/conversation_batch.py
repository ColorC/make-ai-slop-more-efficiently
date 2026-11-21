# [OMNI] origin=codex created_by=codex intent=conversation-attribution-batch-runtime
"""Batch selection and longitudinal aggregation for attributed sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.projects_registry import list_projects

from .authority_projection import canonical_ref
from .conversation_attribution import (
    ConversationAttributionError,
    ConversationAttributionService,
    list_session_candidates,
    parse_codex_transcript,
)
from .config import load_config


_CHANGE_RELATIONS = {"产出", "修改", "纠正"}
_CONSUME_RELATIONS = {"使用", "验证", "审阅"}


def select_batch_candidates(
    *,
    limit: int = 10,
    candidate_limit: int = 240,
    max_size_bytes: int = 4 * 1024 * 1024,
    quiet_seconds: int = 600,
    prefer_untracked: bool = False,
) -> list[dict[str, Any]]:
    """Choose stable main sessions, preferring Project diversity and bindings."""

    now = datetime.now(timezone.utc)
    attribution_settings = load_config().get("conversation_attribution", {})
    output_root = Path(str(attribution_settings.get("output_root") or ""))
    if not output_root.is_absolute():
        output_root = Path.cwd().resolve() / output_root
    project_rows = [row for row in list_projects() if isinstance(row, dict)]
    registered = {
        str(row.get("id") or "")
        for row in project_rows
        if row.get("id")
    }
    project_roots = [
        Path(str(root)).resolve()
        for row in project_rows
        for root in row.get("roots") or []
        if root
    ]
    preferred_projects = {
        str(row.get("id") or "")
        for row in load_config().get("portfolio", {}).get("preferred_projects", [])
        if isinstance(row, dict) and row.get("id")
    }
    try:
        from omnicompany.packages.services.boss_sight.session_attribution import make_resolver

        resolver = make_resolver()
    except Exception:  # noqa: BLE001
        resolver = lambda _sid, _agent: ("", "")  # noqa: E731

    eligible: list[dict[str, Any]] = []
    for row in list_session_candidates(limit=candidate_limit):
        path = Path(str(row["path"]))
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        age_seconds = (now - modified).total_seconds()
        cwd = str(row.get("cwd") or "")
        if row.get("parent_session_id"):
            continue
        if row.get("size_bytes", 0) < 3_000 or row.get("size_bytes", 0) > max_size_bytes:
            continue
        if row.get("source_state") != "archived" and age_seconds < quiet_seconds:
            continue
        lowered_cwd = cwd.lower()
        if "pytest-" in lowered_cwd or "\\temp\\" in lowered_cwd or "/temp/" in lowered_cwd:
            continue
        if cwd and not _overlaps_registered_scope(Path(cwd), project_roots):
            continue
        try:
            preview = parse_codex_transcript(path, tool_output_preview_chars=200)
        except ConversationAttributionError:
            continue
        visible_user_text = "\n".join(
            event.text for event in preview.events if event.kind == "user_message"
        ).strip()
        if not visible_user_text:
            continue
        if visible_user_text.count("?") / max(1, len(visible_user_text)) > 0.2:
            continue
        project, plan = resolver(str(row["session_id"]), "codex")
        project = str(project or "")
        plan = str(plan or "")
        if project not in registered:
            project = ""
        projection_manifest = output_root / str(row["session_id"]) / "manifest.json"
        try:
            needs_tracking = (
                not projection_manifest.is_file()
                or path.stat().st_mtime_ns > projection_manifest.stat().st_mtime_ns
            )
        except OSError:
            needs_tracking = True
        eligible.append(
            {
                "session_id": row["session_id"],
                "session_ref": canonical_ref("session", str(row["session_id"])),
                "path": row["path"],
                "project_ref": canonical_ref("project", project) if project else "",
                "plan_ref": canonical_ref("plan", plan) if "[" in plan else "",
                "preferred_project": project in preferred_projects,
                "needs_tracking": needs_tracking,
                "tracking_state_zh": "尚未归因或已有更新" if needs_tracking else "已有最新投影",
                "size_bytes": row["size_bytes"],
                "modified_at": row["modified_at"],
                "selection_reason_zh": (
                    "统一归属设施已定位，且会话稳定" if project else "归属待语义补充，但会话稳定"
                ),
            }
        )

    eligible.sort(
        key=lambda row: (
            bool(row["preferred_project"]),
            bool(row["project_ref"]),
            int(150_000 <= row["size_bytes"] <= 1_200_000),
            row["modified_at"],
            -int(row["size_bytes"]),
            bool(row["plan_ref"]),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    project_counts: dict[str, int] = defaultdict(int)
    if prefer_untracked:
        untracked = [row for row in eligible if row["needs_tracking"]]
        if len(untracked) < limit:
            selected.extend(untracked)
            for row in untracked:
                project_counts[str(row.get("project_ref") or "")] += 1
            eligible = [row for row in eligible if not row["needs_tracking"]]
        else:
            eligible = untracked
    # 第一轮覆盖重点项目，第二轮各补一个纵向样本，再用其他项目补足广度。
    for row in eligible:
        project_ref = str(row.get("project_ref") or "")
        if row.get("preferred_project") and project_ref and project_counts[project_ref] == 0:
            selected.append(row)
            project_counts[project_ref] += 1
        if len(selected) >= limit:
            return selected
    for row in eligible:
        project_ref = str(row.get("project_ref") or "")
        if (
            row.get("preferred_project")
            and project_ref
            and project_counts[project_ref] == 1
            and int(row["size_bytes"]) <= min(max_size_bytes, 2 * 1024 * 1024)
            and row not in selected
        ):
            selected.append(row)
            project_counts[project_ref] += 1
        if len(selected) >= limit:
            return selected
    for row in eligible:
        project_ref = str(row.get("project_ref") or "")
        if project_ref and project_counts[project_ref] == 0:
            selected.append(row)
            project_counts[project_ref] += 1
        if len(selected) >= limit:
            return selected
    for row in eligible:
        if row not in selected:
            selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _overlaps_registered_scope(cwd: Path, roots: list[Path]) -> bool:
    """Accept workdirs inside, or serving as a parent of, a registered project root."""

    try:
        resolved = cwd.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            if resolved.is_relative_to(root) or root.is_relative_to(resolved):
                return True
        except (OSError, ValueError):
            continue
    return False


async def run_batch(
    service: ConversationAttributionService,
    candidates: list[dict[str, Any]],
    *,
    concurrency: int = 2,
    force: bool = False,
) -> dict[str, Any]:
    """Attribute selected sessions with bounded concurrency and failure isolation."""

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def one(candidate: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await service.attribute_session(candidate["path"], force=force)
            except Exception as exc:  # noqa: BLE001 - one bad historical session must not stop the batch.
                return {
                    "session_ref": candidate["session_ref"],
                    "status": "failed",
                    "status_zh": "失败",
                    "error": str(exc),
                }
            return {
                "session_ref": candidate["session_ref"],
                "status": result["status"],
                "status_zh": result["status_zh"],
                "manifest_path": result["manifest_path"],
                "catalog_path": result.get("catalog_path") or "",
                "context_path": result.get("context_path") or "",
                "measurements_path": result.get("measurements_path") or "",
            }

    results = await asyncio.gather(*(one(candidate) for candidate in candidates))
    stable = json.dumps(
        sorted(item["session_ref"] for item in results),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    batch_id = f"batch-{datetime.now().strftime('%Y%m%d')}-{hashlib.sha256(stable.encode()).hexdigest()[:12]}"
    batch_root = service.output_root / "batches" / batch_id
    batch_root.mkdir(parents=True, exist_ok=True)
    analysis = analyze_batch(results)
    analysis_path = batch_root / "analysis.json"
    _atomic_json(analysis_path, analysis)
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection": {
            "requested": len(candidates),
            "completed": sum(item["status"] in {"complete", "reused"} for item in results),
            "partial": sum(item["status"] == "partial" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "candidate_refs": [item["session_ref"] for item in candidates],
        },
        "session_projections": results,
        "analysis_path": str(analysis_path),
        "copies_session_catalogs": False,
    }
    manifest_path = batch_root / "manifest.json"
    _atomic_json(manifest_path, manifest)
    all_complete = manifest["selection"]["completed"] == manifest["selection"]["requested"]
    return {
        "status": "complete" if all_complete else "partial",
        "status_zh": "完成" if all_complete else "部分完成",
        "batch_id": batch_id,
        "manifest_path": str(manifest_path),
        "analysis_path": str(analysis_path),
        **manifest["selection"],
    }


def analyze_batch(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a cautious, reference-backed first longitudinal assessment."""

    sessions: list[dict[str, Any]] = []
    group_rows: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    corrections: list[dict[str, Any]] = []
    intervention_count = 0
    fact_outcome_count = 0
    independent_outcome_count = 0
    unknown_count = 0

    for result in results:
        if result.get("status") not in {"complete", "reused"}:
            continue
        manifest = _read_json(Path(str(result.get("manifest_path") or "")))
        catalog = _read_json(Path(str(result.get("catalog_path") or "")))
        context = _read_json(Path(str(result.get("context_path") or "")))
        measurements = _read_json(Path(str(result.get("measurements_path") or "")))
        source_path = Path(str((manifest.get("source") or {}).get("path") or ""))
        event_kinds = _evidence_kinds(source_path)
        project_refs = [
            str(item.get("target_ref") or "")
            for item in context.get("relationships") or []
            if str(item.get("target_ref") or "").startswith("omni://project/")
        ]
        session_ref = str(context.get("session_ref") or result.get("session_ref") or "")
        project_relationships = [
            item
            for item in context.get("relationships") or []
            if str(item.get("target_ref") or "").startswith("omni://project/")
        ]
        metric_values = {
            name: (row.get("value") if isinstance(row, dict) else None)
            for name, row in (measurements.get("derived_metrics") or {}).items()
        }
        session_row = {
            "session_ref": session_ref,
            "manifest_ref": str(result.get("manifest_path") or ""),
            "catalog_ref": str(result.get("catalog_path") or ""),
            "project_refs": sorted(set(project_refs)),
            "modified_at": str((manifest.get("source") or {}).get("modified_at") or ""),
            "scope_source_zh": (
                "显式绑定账本"
                if any(
                    "cc_session_bindings.json" in str(item.get("authority_pointer") or "")
                    for item in project_relationships
                )
                else "统一归属设施推断"
            ),
            "derived_metrics": metric_values,
            "usage_ref": str((measurements.get("usage") or {}).get("source_ref") or ""),
            "business_paths": [],
            "human_interventions": 0,
            "fact_outcomes": 0,
            "independent_outcomes": 0,
            "unknowns": 0,
        }
        sessions.append(session_row)
        for work_item in catalog.get("work_items") or []:
            item_projects = [
                canonical_ref("project", str(project_id))
                for project_id in work_item.get("project_ids") or []
                if project_id
            ] or project_refs or [""]
            business_path = tuple(str(value) for value in work_item.get("business_path") or [])
            outcomes = list(work_item.get("outcomes") or [])
            session_row["business_paths"].append(list(business_path))
            independent = False
            for outcome in outcomes:
                if outcome.get("certainty") != "fact":
                    continue
                fact_outcome_count += 1
                session_row["fact_outcomes"] += 1
                if any(
                    event_kinds.get(ref) in {"tool_result", "user_message"}
                    for ref in outcome.get("evidence_refs") or []
                ):
                    independent = True
                    independent_outcome_count += 1
                    session_row["independent_outcomes"] += 1
            work_row = {
                "session_ref": session_ref,
                "modified_at": session_row["modified_at"],
                "business_path": list(business_path),
                "canonical_refs": list(work_item.get("canonical_refs") or []),
                "has_independent_outcome": independent,
                "outcomes": outcomes,
            }
            for project_ref in item_projects:
                group_rows[(project_ref, business_path)].append(work_row)
            intervention_count += len(work_item.get("human_interventions") or [])
            unknown_count += len(work_item.get("unknowns") or [])
            session_row["human_interventions"] += len(
                work_item.get("human_interventions") or []
            )
            session_row["unknowns"] += len(work_item.get("unknowns") or [])
            for correction in work_item.get("user_corrections") or []:
                corrections.append(
                    {
                        "summary": str(correction.get("summary") or ""),
                        "evidence_refs": list(correction.get("evidence_refs") or []),
                        "session_ref": session_ref,
                    }
                )

    comparable_groups: list[dict[str, Any]] = []
    consumption_chains: list[dict[str, Any]] = []
    for (project_ref, business_path), rows in group_rows.items():
        unique_sessions = sorted({row["session_ref"] for row in rows})
        if len(unique_sessions) >= 2:
            comparable_groups.append(
                {
                    "project_ref": project_ref,
                    "business_path": list(business_path),
                    "session_refs": unique_sessions,
                    "status_zh": "仍需核对任务目标、难度和验收口径",
                }
            )
        ordered = sorted(rows, key=lambda row: row["modified_at"])
        changes: dict[str, dict[str, Any]] = {}
        for row in ordered:
            for reference in row["canonical_refs"]:
                ref = str(reference.get("ref") or "")
                relation = str(reference.get("relation") or "")
                if not ref:
                    continue
                if relation in _CHANGE_RELATIONS:
                    changes.setdefault(ref, row)
                elif relation in _CONSUME_RELATIONS and ref in changes:
                    previous = changes[ref]
                    if previous["session_ref"] == row["session_ref"]:
                        continue
                    consumption_chains.append(
                        {
                            "project_ref": project_ref,
                            "business_path": list(business_path),
                            "learning_ref": ref,
                            "change_session_ref": previous["session_ref"],
                            "consumption_session_ref": row["session_ref"],
                            "has_independent_later_outcome": row["has_independent_outcome"],
                        }
                    )

    project_refs = sorted(
        {
            project_ref
            for session in sessions
            for project_ref in session.get("project_refs") or []
            if project_ref
        }
    )
    sessions_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for session in sessions:
        for project_ref in session.get("project_refs") or []:
            sessions_by_project[project_ref].append(session)
    repeated_projects = []
    for project_ref, rows in sorted(sessions_by_project.items()):
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda row: row["modified_at"])
        repeated_projects.append(
            {
                "project_ref": project_ref,
                "status_zh": "仅同 Project；Business 与任务口径不同，不据此判断改善",
                "sessions": [
                    {
                        "session_ref": row["session_ref"],
                        "modified_at": row["modified_at"],
                        "business_paths": row["business_paths"],
                        "derived_metrics": row["derived_metrics"],
                        "usage_ref": row["usage_ref"],
                        "human_interventions": row["human_interventions"],
                        "fact_outcomes": row["fact_outcomes"],
                        "independent_outcomes": row["independent_outcomes"],
                        "unknowns": row["unknowns"],
                    }
                    for row in ordered
                ],
            }
        )
    inferred_scope_sessions = [
        session["session_ref"]
        for session in sessions
        if session["project_refs"] and session["scope_source_zh"] == "统一归属设施推断"
    ]
    complete_episodes = [
        chain for chain in consumption_chains if chain["has_independent_later_outcome"]
    ]
    if complete_episodes:
        conclusion = (
            "已找到改动被后续任务消费并出现独立结果的链，但仍缺同口径基线，"
            "目前只能确认学习流动，不能断言工作质量改善。"
        )
    elif consumption_chains:
        conclusion = (
            "已找到改动被后续会话消费的线索，但后续独立结果不足，暂时无法判断工作质量是否改善。"
        )
    else:
        conclusion = (
            "这批数据足以还原会话在做什么及人工纠正，但尚未形成“改动—后续消费—独立结果—同口径基线”的闭环，"
            "因此还不能判断 AI 是否在工作中持续改善。"
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "核心问题": "AI 是否有在工作过程中改善自己的工作质量？",
        "当前结论": conclusion,
        "口径": {
            "不生成总分": True,
            "可比要求": "同 Project / Business，任务目标、难度、环境和验收口径可核对，并有改动消费与独立结果",
            "独立结果暂定": "工具结果或用户消息直接支持的事实型 outcome；仍不等于最终业务验收",
        },
        "覆盖": {
            "完成会话": len(sessions),
            "项目": len(project_refs),
            "项目引用": project_refs,
            "Business组": len(group_rows),
        },
        "观察信号": {
            "用户纠正": len(corrections),
            "人工介入": intervention_count,
            "事实型结果": fact_outcome_count,
            "有外部支持的事实型结果": independent_outcome_count,
            "明确未知": unknown_count,
            "纠正样本": _dedupe_samples(corrections, limit=10),
        },
        "比较准备度": {
            "同Project但不同Business的观察组": repeated_projects,
            "出现至少两个会话的同Project/Business组": comparable_groups,
            "改动被后续消费的链": consumption_chains,
            "带后续独立结果的消费链": complete_episodes,
            "可确认改善的闭环数": 0,
        },
        "数据质量": {
            "Project归属仅来自统一设施推断的会话数": len(inferred_scope_sessions),
            "对应会话": inferred_scope_sessions,
            "解释": "这些归属仍引用唯一设施，但缺少显式会话绑定；应作为比较置信度限制，而不是在本项目另行改绑。",
        },
        "session_refs": [session["session_ref"] for session in sessions],
    }


def _evidence_kinds(source_path: Path) -> dict[str, str]:
    if not source_path.is_file():
        return {}
    try:
        transcript = parse_codex_transcript(source_path)
    except ConversationAttributionError:
        return {}
    return {
        event.evidence_ref(transcript.session_id): event.kind
        for event in transcript.events
    }


def _dedupe_samples(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        summary = str(row.get("summary") or "").strip()
        if not summary or summary in seen:
            continue
        seen.add(summary)
        result.append(row)
        if len(result) >= limit:
            break
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


__all__ = ["analyze_batch", "run_batch", "select_batch_candidates"]
