# [OMNI] origin=codex created_by=codex intent=project-authority-evidence-projection
"""Reference-only joins for conversation attribution.

This module does not own session, project, plan, task, decision, review, usage,
or tool-trace facts.  It resolves the existing authorities into foreign-key
relationships and computes only metrics that are not already stored directly.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from omnicompany.core.projects_registry import list_projects


AUTHORITY_CONTEXT_VERSION = 2
MEASUREMENT_VERSION = 1


def canonical_ref(kind: str, value: str, *, anchor: str = "") -> str:
    """Build the workspace's typed reference without copying object metadata."""

    encoded = quote(str(value).strip(), safe="")
    suffix = f"#{anchor}" if anchor else ""
    return f"omni://{kind}/{encoded}{suffix}"


def build_authority_context(
    transcript: Any,
    workspace: str | Path,
) -> dict[str, Any]:
    """Join existing identity authorities into a reference-only context."""

    root = Path(workspace).resolve()
    session_id = str(transcript.session_id)
    session_ref = canonical_ref("session", session_id)
    bindings_path = root / "data" / "cc_session_bindings.json"
    sessions_path = root / "data" / "cc_sessions.json"
    binding_store = _read_mapping(bindings_path)
    session_store = _read_mapping(sessions_path)
    binding_rows = _matching_rows(binding_store, session_id)
    session_rows = _matching_rows(session_store, session_id)

    # The unified resolver remains the only Project / Plan classifier.  We use
    # exact ledger values only to explain whether its result came from a direct
    # binding; no second classifier is introduced here.
    project_id = ""
    plan_id = ""
    task_ref = ""
    for _, row in binding_rows:
        project_id = project_id or str(row.get("project") or "").strip()
        plan_id = plan_id or str(row.get("active_plan") or row.get("plan") or "").strip()
        task_id = str(row.get("task_id") or "").strip()
        if task_id and not task_ref:
            task_ref = canonical_ref("task", task_id)
    for _, row in session_rows:
        project_id = project_id or str(row.get("project") or row.get("active_project") or "").strip()
        plan_id = plan_id or str(row.get("active_plan") or row.get("plan") or "").strip()

    resolved_project, resolved_plan = _resolve_session_scope(session_id)
    registered_projects = _registered_project_ids()
    if not project_id and resolved_project in registered_projects:
        project_id = resolved_project
    if not plan_id and "[" in resolved_plan:
        plan_id = resolved_plan

    relationships: list[dict[str, Any]] = [
        {
            "relation": "原始会话",
            "target_ref": session_ref,
            "authority": "原生会话文件",
            "authority_pointer": str(transcript.source_path),
        }
    ]
    if project_id in registered_projects:
        relationships.append(
            {
                "relation": "归属项目",
                "target_ref": canonical_ref("project", project_id),
                "authority": "会话归属统一设施",
                "authority_pointer": _scope_pointer(bindings_path, binding_rows, "project"),
            }
        )
    if plan_id:
        relationships.append(
            {
                "relation": "归属计划",
                "target_ref": canonical_ref("plan", plan_id),
                "authority": "会话归属统一设施",
                "authority_pointer": _scope_pointer(bindings_path, binding_rows, "active_plan"),
            }
        )
    if task_ref:
        relationships.append(
            {
                "relation": "归属任务",
                "target_ref": task_ref,
                "authority": "progress-service / 会话绑定账本",
                "authority_pointer": _scope_pointer(bindings_path, binding_rows, "task_id"),
            }
        )

    default_scope = {
        "project_ref": canonical_ref("project", project_id) if project_id in registered_projects else "",
        "plan_ref": canonical_ref("plan", plan_id) if plan_id else "",
        "task_ref": task_ref,
        "authority": "会话归属统一设施",
    }
    scope_segments = _scope_segments(
        binding_rows,
        transcript=transcript,
        registered_projects=registered_projects,
        bindings_path=bindings_path,
    )
    for segment in scope_segments:
        for relation, field in (
            ("分段归属项目", "project_ref"),
            ("分段归属计划", "plan_ref"),
            ("分段归属任务", "task_ref"),
        ):
            target_ref = str(segment.get(field) or "")
            if target_ref:
                relationships.append(
                    {
                        "relation": relation,
                        "target_ref": target_ref,
                        "authority": "会话分段绑定",
                        "authority_pointer": str(segment["authority_pointer"]),
                    }
                )

    for binding_key, row in binding_rows:
        for index, record in enumerate(row.get("records") or []):
            if not isinstance(record, dict):
                continue
            target_ref = _record_target_ref(record)
            if not target_ref:
                continue
            relationships.append(
                {
                    "relation": "会话关联产物",
                    "target_ref": target_ref,
                    "authority": "会话绑定账本",
                    "authority_pointer": (
                        f"{bindings_path}#/{binding_key}/records/{index}"
                    ),
                }
            )

    relationships = _unique_relationships(relationships)
    authoritative_refs = sorted(
        {str(item["target_ref"]) for item in relationships if item.get("target_ref")}
    )
    usage_ref = _find_ccusage_ref(root, session_id)
    return {
        "schema_version": AUTHORITY_CONTEXT_VERSION,
        "session_ref": session_ref,
        "source_pointer": str(transcript.source_path),
        "relationships": relationships,
        "authoritative_refs": authoritative_refs,
        "default_scope": default_scope,
        "scope_segments": scope_segments,
        "usage_ref": usage_ref,
        "ownership": {
            "session": "原生会话文件",
            "scope": "boss_sight.session_attribution",
            "bindings": "data/cc_session_bindings.json",
            "review_context": "ReviewStage Context Spine",
            "usage": "ccusage_stats",
            "tool_trace": "trace_induction.external_trace_index",
        },
        "copies_authority_payloads": False,
    }


def build_measurements(transcript: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Compute only derived aggregates; usage values remain in ccusage."""

    timestamps: list[tuple[datetime, str]] = []
    turn_durations_ms: list[float] = []
    first_token_ms: list[float] = []
    for event in transcript.events:
        parsed = _parse_datetime(str(event.timestamp or ""))
        if parsed is not None:
            timestamps.append((parsed, event.evidence_ref(transcript.session_id)))
        if event.kind != "turn_complete":
            continue
        try:
            payload = json.loads(event.text)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload.get("duration_ms"), (int, float)):
            turn_durations_ms.append(float(payload["duration_ms"]))
        if isinstance(payload.get("time_to_first_token_ms"), (int, float)):
            first_token_ms.append(float(payload["time_to_first_token_ms"]))

    wall_clock_seconds: float | None = None
    wall_clock_refs: list[str] = []
    if len(timestamps) >= 2:
        timestamps.sort(key=lambda item: item[0])
        wall_clock_seconds = max(
            0.0, (timestamps[-1][0] - timestamps[0][0]).total_seconds()
        )
        wall_clock_refs = [timestamps[0][1], timestamps[-1][1]]

    counts = Counter(event.kind for event in transcript.semantic_events)
    review_refs = [
        item["target_ref"]
        for item in context.get("relationships") or []
        if str(item.get("target_ref") or "").startswith("omni://review/")
    ]
    limitations = []
    if not context.get("usage_ref"):
        limitations.append("当前 ccusage 缓存中没有定位到该会话的用量行")
    if not review_refs:
        limitations.append("没有可直接关联的 Review；首次可验收结果仍未知")
    if not any(item.get("relation") == "归属任务" for item in context.get("relationships") or []):
        limitations.append("会话未绑定到权威 Task，任务级比较暂不可靠")

    return {
        "schema_version": MEASUREMENT_VERSION,
        "session_ref": context["session_ref"],
        "derived_metrics": {
            "墙钟秒数": {
                "value": round(wall_clock_seconds, 3) if wall_clock_seconds is not None else None,
                "definition": "首个与末个可解析会话事件时间戳之差",
                "evidence_refs": wall_clock_refs,
            },
            "Agent活跃轮次秒数": {
                "value": round(sum(turn_durations_ms) / 1000.0, 3) if turn_durations_ms else None,
                "definition": "原生 task_complete.duration_ms 求和；不等于墙钟时间",
                "evidence_refs": [
                    event.evidence_ref(transcript.session_id)
                    for event in transcript.events
                    if event.kind == "turn_complete"
                ],
            },
            "首次Token毫秒中位数": {
                "value": _median(first_token_ms),
                "definition": "原生 task_complete.time_to_first_token_ms 的中位数",
                "evidence_refs": [
                    event.evidence_ref(transcript.session_id)
                    for event in transcript.events
                    if event.kind == "turn_complete"
                ],
            },
            "工具调用次数": {
                "value": int(counts.get("tool_call", 0)),
                "definition": "原生会话 function/custom tool call 事件计数",
                "evidence_refs": [],
            },
            "可见用户消息次数": {
                "value": int(counts.get("user_message", 0)),
                "definition": "排除系统、插件和环境注入后的用户消息计数",
                "evidence_refs": [],
            },
        },
        "usage": {
            "status_zh": "已引用" if context.get("usage_ref") else "暂未定位",
            "authority": "ccusage_stats",
            "source_ref": context.get("usage_ref") or "",
            "values_copied": False,
        },
        "acceptance": {
            "status_zh": "有Review关联但尚未解析首次裁定" if review_refs else "尚无可定位的独立验收裁定",
            "review_refs": sorted(set(review_refs)),
        },
        "limitations_zh": limitations,
    }


def allowed_canonical_refs(context: dict[str, Any]) -> set[str]:
    return {
        str(item)
        for item in context.get("authoritative_refs") or []
        if isinstance(item, str) and item
    }


def _resolve_session_scope(session_id: str) -> tuple[str, str]:
    try:
        from omnicompany.packages.services.boss_sight.session_attribution import make_resolver

        project, plan = make_resolver()(session_id, "codex")
        return str(project or ""), str(plan or "")
    except Exception:  # noqa: BLE001 - authority can be unavailable in isolated tests.
        return "", ""


def _registered_project_ids() -> set[str]:
    try:
        return {
            str(item.get("id") or "")
            for item in list_projects()
            if isinstance(item, dict) and item.get("id")
        }
    except Exception:  # noqa: BLE001
        return set()


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _matching_rows(store: dict[str, Any], session_id: str) -> list[tuple[str, dict[str, Any]]]:
    target = session_id.lower()
    rows: list[tuple[str, dict[str, Any]]] = []
    for key, value in store.items():
        if not isinstance(value, dict):
            continue
        identifiers = {
            str(key).lower(),
            str(value.get("id") or "").lower(),
            str(value.get("session_id") or "").lower(),
            str(value.get("claude_session_id") or "").lower(),
            str(value.get("provider_session_id") or "").lower(),
            str(value.get("conversation_id") or "").lower(),
            str(value.get("trace_id") or "").lower(),
        }
        if target in identifiers or f"codex_{target}" in identifiers:
            rows.append((str(key), value))
    rows.sort(
        key=lambda item: (
            bool(item[1].get("project") or item[1].get("active_plan") or item[1].get("task_id")),
            str(item[1].get("updated_at") or ""),
        ),
        reverse=True,
    )
    return rows


def _scope_pointer(path: Path, rows: list[tuple[str, dict[str, Any]]], field: str) -> str:
    for key, row in rows:
        if row.get(field):
            return f"{path}#/{key}/{field}"
    return "boss_sight.session_attribution"


def _scope_segments(
    rows: list[tuple[str, dict[str, Any]]],
    *,
    transcript: Any,
    registered_projects: set[str],
    bindings_path: Path,
) -> list[dict[str, Any]]:
    """Project authoritative scope history onto stable transcript coordinates."""

    events = list(transcript.events)
    projected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for binding_key, row in rows:
        for index, raw in enumerate(row.get("scope_segments") or []):
            if not isinstance(raw, dict):
                continue
            segment_id = str(raw.get("segment_id") or f"scope-{index + 1:04d}")
            key = (binding_key, segment_id)
            if key in seen:
                continue
            seen.add(key)
            project_id = str(raw.get("project") or "").strip()
            plan_id = str(raw.get("active_plan") or raw.get("plan") or "").strip()
            task_id = str(raw.get("task_id") or "").strip()
            start_ref = str(raw.get("start_event_ref") or "").strip()
            end_ref = str(raw.get("end_event_ref") or "").strip()
            start_line = raw.get("start_source_line")
            end_line = raw.get("end_source_line")
            started_at = _parse_datetime(str(raw.get("started_at") or ""))
            ended_at = _parse_datetime(str(raw.get("ended_at") or ""))
            matching = []
            for event in events:
                event_time = _parse_datetime(str(event.timestamp or ""))
                if start_line is not None and event.source_line < int(start_line):
                    continue
                if end_line is not None and event.source_line > int(end_line):
                    continue
                if started_at is not None and event_time is not None and event_time < started_at:
                    continue
                if ended_at is not None and event_time is not None and event_time >= ended_at:
                    continue
                matching.append(event)
            if not start_ref and matching:
                start_ref = matching[0].evidence_ref(transcript.session_id)
            if not end_ref and matching:
                end_ref = matching[-1].evidence_ref(transcript.session_id)
            projected.append(
                {
                    "segment_id": segment_id,
                    "binding_state": str(raw.get("binding_state") or "candidate"),
                    "source": str(raw.get("source") or "unknown"),
                    "rationale": str(raw.get("rationale") or ""),
                    "start_event_ref": start_ref,
                    "end_event_ref": end_ref,
                    "start_source_line": start_line,
                    "end_source_line": end_line,
                    "started_at": str(raw.get("started_at") or ""),
                    "ended_at": str(raw.get("ended_at") or ""),
                    "project_ref": (
                        canonical_ref("project", project_id)
                        if project_id in registered_projects
                        else ""
                    ),
                    "plan_ref": canonical_ref("plan", plan_id) if plan_id else "",
                    "task_ref": canonical_ref("task", task_id) if task_id else "",
                    "evidence_refs": list(raw.get("evidence_refs") or []),
                    "authority_pointer": f"{bindings_path}#/{binding_key}/scope_segments/{index}",
                }
            )
    return projected


def _record_target_ref(record: dict[str, Any]) -> str:
    kind = str(record.get("kind") or "")
    record_id = str(record.get("id") or "").strip()
    if kind == "decision" and record_id:
        return canonical_ref("decision", record_id)
    if kind == "review_material" and record_id:
        return canonical_ref("review", record_id, anchor="context-spine")
    if kind in {"task", "task_note"} and record_id:
        return canonical_ref("task", record_id)
    raw_ref = str(record.get("ref") or "").strip()
    if raw_ref.startswith("plan:"):
        return canonical_ref("plan", raw_ref.removeprefix("plan:"))
    return ""


def _unique_relationships(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("relation") or ""),
            str(row.get("target_ref") or ""),
            str(row.get("authority_pointer") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _find_ccusage_ref(root: Path, session_id: str) -> str:
    cache_root = root / "data" / "boss_sight" / "ccusage_cache"
    try:
        files = sorted(cache_root.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    except OSError:
        return ""
    for path in files[:20]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for index, row in enumerate(payload.get("sessions") or []):
            sid = str(row.get("sid") or row.get("period") or "")
            if session_id.lower() in sid.lower():
                return f"{path}#/sessions/{index}"
    return ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    center = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[center], 3)
    return round((ordered[center - 1] + ordered[center]) / 2.0, 3)


__all__ = [
    "AUTHORITY_CONTEXT_VERSION",
    "MEASUREMENT_VERSION",
    "allowed_canonical_refs",
    "build_authority_context",
    "build_measurements",
    "canonical_ref",
]
