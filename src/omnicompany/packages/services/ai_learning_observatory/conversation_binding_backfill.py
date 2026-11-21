# [OMNI] origin=codex created_by=codex intent=conversation-scope-binding-backfill
"""Backfill event-bounded Project/Plan/Task scopes from conversation projections.

The canonical binding ledger remains ``data/cc_session_bindings.json``. This
module reads immutable session projections and writes only scope identifiers and
source coordinates back through the identity service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

from omnicompany.packages.services._core.identity import (
    all_session_bindings,
    prune_generated_session_scope_segments,
    set_session_default_scope_from_history,
    update_session_binding,
    upsert_session_scope_segment,
)

from .conversation_attribution import (
    ConversationAttributionService,
    _run_record,
    _structured_payload,
    parse_codex_transcript,
)
from .agent_runtime import agent_tool_calls


_PLAN_USE_RE = re.compile(
    r"\bomni(?:\.exe)?\s+plan\s+use\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))",
    re.IGNORECASE,
)
_SESSION_OPTION_RE = {
    "active_plan": re.compile(r"--(?:plan|active-plan)\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))", re.IGNORECASE),
    "project": re.compile(r"--project\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))", re.IGNORECASE),
    "task_id": re.compile(r"--task\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))", re.IGNORECASE),
}


def backfill_batch_bindings(
    batch_manifest_path: str | Path,
    *,
    workspace: str | Path,
    dry_run: bool = False,
    plan_suggestions: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Backfill one completed batch and return a Chinese-facing audit summary."""

    root = Path(workspace).resolve()
    batch_path = Path(batch_manifest_path).resolve()
    batch = _read_json(batch_path)
    plan_catalog = _plan_catalog(root)
    results: list[dict[str, Any]] = []
    totals = {"会话": 0, "确认分段": 0, "候选分段": 0, "跳过工作项": 0, "失败": 0}
    for projection in batch.get("session_projections") or []:
        try:
            result = _backfill_projection(
                projection,
                workspace=root,
                plan_catalog=plan_catalog,
                dry_run=dry_run,
                plan_suggestions=(plan_suggestions or {}).get(
                    str(projection.get("session_ref") or "").rsplit("/", 1)[-1], {}
                ),
            )
        except Exception as exc:  # noqa: BLE001 - batch failure isolation is intentional.
            result = {
                "会话": str(projection.get("session_ref") or "未知会话"),
                "状态": "失败",
                "原因": str(exc),
            }
            totals["失败"] += 1
        else:
            totals["会话"] += 1
            totals["确认分段"] += int(result["确认分段"])
            totals["候选分段"] += int(result["候选分段"])
            totals["跳过工作项"] += int(result["跳过工作项"])
        results.append(result)
    return {
        "状态": "完成" if totals["失败"] == 0 else "部分完成",
        "批次": str(batch.get("batch_id") or batch_path.parent.name),
        "只预览不写入": dry_run,
        **totals,
        "结果": results,
    }


async def backfill_batch_bindings_with_semantic_plans(
    batch_manifest_path: str | Path,
    *,
    workspace: str | Path,
    dry_run: bool = False,
    concurrency: int = 2,
) -> dict[str, Any]:
    """Use DeepSeek only for missing Plan candidates, then write bounded scopes."""

    root = Path(workspace).resolve()
    suggestions, semantic = await _semantic_plan_suggestions(
        Path(batch_manifest_path).resolve(),
        workspace=root,
        concurrency=concurrency,
    )
    result = backfill_batch_bindings(
        batch_manifest_path,
        workspace=root,
        dry_run=dry_run,
        plan_suggestions=suggestions,
    )
    result["Plan候选整理"] = semantic
    return result


def latest_batch_manifest(workspace: str | Path) -> Path:
    root = (
        Path(workspace).resolve()
        / "data"
        / "services"
        / "ai_learning_observatory"
        / "conversation_attribution"
        / "batches"
    )
    manifests = sorted(
        root.glob("batch-*/manifest.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not manifests:
        raise FileNotFoundError("尚无可回填的会话归因批次")
    return manifests[0]


def _backfill_projection(
    projection: dict[str, Any],
    *,
    workspace: Path,
    plan_catalog: dict[str, dict[str, str]],
    dry_run: bool,
    plan_suggestions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    catalog = _read_json(Path(str(projection.get("catalog_path") or "")))
    manifest = _read_json(Path(str(projection.get("manifest_path") or "")))
    source_path = Path(str((manifest.get("source") or {}).get("path") or ""))
    transcript = parse_codex_transcript(source_path)
    session_id = transcript.session_id
    trace_id = _trace_id_for_session(session_id)
    binding_record = all_session_bindings().get(trace_id) or {}
    events = list(transcript.events)
    event_by_ref = {event.evidence_ref(session_id): event for event in events}
    explicit = _explicit_scope_changes(
        events,
        session_id,
        plan_catalog,
        initial_scope={
            "project": str(binding_record.get("project") or ""),
            "active_plan": str(binding_record.get("active_plan") or ""),
            "task_id": str(binding_record.get("task_id") or ""),
        },
    )
    confirmed_ranges: list[tuple[int, int]] = []
    writes: list[dict[str, Any]] = []

    for index, change in enumerate(explicit):
        start = int(change["ordinal"])
        end = int(explicit[index + 1]["ordinal"]) - 1 if index + 1 < len(explicit) else len(events)
        confirmed_ranges.append((start, end))
        writes.append(
            {
                "segment_id": _segment_id(session_id, start, end, change),
                "project": change.get("project") or "",
                "active_plan": change.get("active_plan") or "",
                "task_id": change.get("task_id") or "",
                "start_event_ref": events[start - 1].evidence_ref(session_id),
                "end_event_ref": events[end - 1].evidence_ref(session_id),
                "start_source_line": events[start - 1].source_line,
                "end_source_line": events[end - 1].source_line,
                "binding_state": "confirmed",
                "source": "retrospective_explicit",
                "rationale": "原会话中存在显式 plan/session 绑定命令。",
                "evidence_refs": [str(change["evidence_ref"])],
            }
        )

    skipped = 0
    for item in catalog.get("work_items") or []:
        refs = [str(ref) for ref in item.get("evidence_refs") or [] if ref in event_by_ref]
        ordinals = sorted(
            int(event_by_ref[ref].event_id.removeprefix("e")) for ref in refs
        )
        if not ordinals:
            skipped += 1
            continue
        start, end = ordinals[0], ordinals[-1]
        if any(start <= high and end >= low for low, high in confirmed_ranges):
            continue
        project_ids = [str(value) for value in item.get("project_ids") or [] if value]
        plan_ids = list(
            dict.fromkeys(
                _canonical_values(item.get("canonical_refs") or [], "plan")
                + [str(value) for value in item.get("plan_ids") or [] if value]
            )
        )
        task_ids = list(
            dict.fromkeys(
                _canonical_values(item.get("canonical_refs") or [], "task")
                + [str(value) for value in item.get("task_ids") or [] if value]
            )
        )
        suggestion = plan_suggestions.get(str(item.get("work_item_id") or "")) or {}
        if not plan_ids and suggestion.get("state") == "candidate" and suggestion.get("plan_id"):
            plan_ids = [str(suggestion["plan_id"])]
        if len(project_ids) != 1 and len(plan_ids) != 1 and len(task_ids) != 1:
            skipped += 1
            continue
        project = project_ids[0] if len(project_ids) == 1 else ""
        plan = plan_ids[0] if len(plan_ids) == 1 else ""
        task = task_ids[0] if len(task_ids) == 1 else ""
        if plan and not project:
            project = str((plan_catalog.get(plan) or {}).get("project") or "")
        writes.append(
            {
                "segment_id": _segment_id(
                    session_id,
                    start,
                    end,
                    {"project": project, "active_plan": plan, "task_id": task},
                ),
                "project": project,
                "active_plan": plan,
                "task_id": task,
                "start_event_ref": events[start - 1].evidence_ref(session_id),
                "end_event_ref": events[end - 1].evidence_ref(session_id),
                "start_source_line": events[start - 1].source_line,
                "end_source_line": events[end - 1].source_line,
                "binding_state": "candidate",
                "source": "retrospective_semantic",
                "rationale": str(suggestion.get("rationale") or "")
                or "沿用已校验会话工作项目录的候选归属；未冒充显式绑定。",
                "evidence_refs": list(
                    dict.fromkeys(
                        list(suggestion.get("evidence_refs") or []) + refs[:4] + refs[-4:]
                    )
                ),
            }
        )

    unique_writes: dict[str, dict[str, Any]] = {}
    for row in writes:
        existing = unique_writes.get(str(row["segment_id"]))
        if existing is None or row["binding_state"] == "confirmed":
            unique_writes[str(row["segment_id"])] = row
    writes = list(unique_writes.values())

    if not dry_run:
        update_session_binding(trace_id, session_id=session_id, provider="codex")
        for row in writes:
            upsert_session_scope_segment(trace_id, **row)
        prune_generated_session_scope_segments(
            trace_id,
            sources={"retrospective_explicit", "retrospective_semantic"},
            keep_segment_ids={str(row["segment_id"]) for row in writes},
        )
        if explicit:
            latest = explicit[-1]
            set_session_default_scope_from_history(
                trace_id,
                project=str(latest.get("project") or "") or None,
                active_plan=str(latest.get("active_plan") or "") or None,
                task_id=str(latest.get("task_id") or "") or None,
            )

    return {
        "会话": session_id,
        "trace_id": trace_id,
        "状态": "完成",
        "确认分段": sum(1 for row in writes if row["binding_state"] == "confirmed"),
        "候选分段": sum(1 for row in writes if row["binding_state"] == "candidate"),
        "跳过工作项": skipped,
    }


def _explicit_scope_changes(
    events: Iterable[Any],
    session_id: str,
    plan_catalog: dict[str, dict[str, str]],
    *,
    initial_scope: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    current: dict[str, str] = {"project": "", "active_plan": "", "task_id": ""}
    current.update(initial_scope or {})
    for event in events:
        if str(event.kind or "") != "tool_call":
            continue
        text = _tool_command_text(str(event.text or ""))
        values: dict[str, str] = {}
        plan_match = _PLAN_USE_RE.search(text)
        if plan_match:
            values["active_plan"] = _resolve_plan_query(_match_value(plan_match), plan_catalog)
        if re.search(r"\bomni(?:\.exe)?\s+session\s+bind\b", text, re.IGNORECASE):
            for field, pattern in _SESSION_OPTION_RE.items():
                match = pattern.search(text)
                if match:
                    value = _match_value(match)
                    values[field] = (
                        _resolve_plan_query(value, plan_catalog) if field == "active_plan" else value
                    )
        values = {key: value for key, value in values.items() if value and not value.startswith("$")}
        if not values:
            continue
        current.update(values)
        plan = current.get("active_plan") or ""
        if plan and not current.get("project"):
            current["project"] = str((plan_catalog.get(plan) or {}).get("project") or "")
        changes.append(
            {
                **current,
                "ordinal": int(event.event_id.removeprefix("e")),
                "evidence_ref": event.evidence_ref(session_id),
            }
        )
    return changes


def _plan_catalog(workspace: Path) -> dict[str, dict[str, str]]:
    plans_root = workspace / "docs" / "plans"
    catalog: dict[str, dict[str, str]] = {}
    for plan_md in plans_root.rglob("plan.md"):
        plan_id = plan_md.parent.relative_to(plans_root).as_posix()
        text = plan_md.read_text(encoding="utf-8", errors="replace")
        project_match = re.search(r"(?m)^project:\s*['\"]?([^'\"\r\n]+)", text)
        title_match = re.search(r"(?m)^title:\s*['\"]?([^'\"\r\n]+)", text)
        status_match = re.search(r"(?m)^status:\s*['\"]?([^'\"\r\n]+)", text)
        work_type_match = re.search(r"(?m)^work_type:\s*['\"]?([^'\"\r\n]+)", text)
        catalog[plan_id] = {
            "project": project_match.group(1).strip() if project_match else "",
            "basename": plan_md.parent.name,
            "title": title_match.group(1).strip() if title_match else "",
            "status": status_match.group(1).strip() if status_match else "",
            "work_type": work_type_match.group(1).strip() if work_type_match else "",
            "path": str(plan_md.resolve()),
        }
    return catalog


def _resolve_plan_query(value: str, catalog: dict[str, dict[str, str]]) -> str:
    normalized = value.strip().strip("`\"").replace("\\", "/")
    normalized = normalized.removeprefix("docs/plans/").removesuffix("/plan.md").rstrip("/")
    if normalized in catalog:
        return normalized
    matches = [plan_id for plan_id, row in catalog.items() if row["basename"] == normalized]
    return matches[0] if len(matches) == 1 else ""


def _canonical_values(rows: Iterable[dict[str, Any]], kind: str) -> list[str]:
    prefix = f"omni://{kind}/"
    return list(
        dict.fromkeys(
            unquote(str(row.get("ref") or "").removeprefix(prefix))
            for row in rows
            if isinstance(row, dict) and str(row.get("ref") or "").startswith(prefix)
        )
    )


def _trace_id_for_session(session_id: str) -> str:
    target = session_id.lower()
    for key, row in all_session_bindings().items():
        identifiers = {
            str(key).lower(),
            str(row.get("trace_id") or "").lower(),
            str(row.get("session_id") or "").lower(),
            str(row.get("claude_session_id") or "").lower(),
        }
        if target in identifiers or f"codex_{target}" in identifiers:
            return str(key)
    return f"codex_{session_id}"


def _segment_id(session_id: str, start: int, end: int, scope: dict[str, Any]) -> str:
    del scope  # Scope can be revised while the source range remains the same.
    raw = json.dumps([session_id, start, end], ensure_ascii=False, separators=(",", ":"))
    return "history-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _match_value(match: re.Match[str]) -> str:
    return next((value for value in match.groups() if value is not None), "").strip()


def _tool_command_text(value: str) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
        return str(parsed["command"])
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


async def _semantic_plan_suggestions(
    batch_path: Path,
    *,
    workspace: Path,
    concurrency: int,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    batch = _read_json(batch_path)
    plan_catalog = _plan_catalog(workspace)
    service = ConversationAttributionService()
    schema_path = Path(str(service.settings.get("binding_schema_path") or ""))
    if not schema_path.is_absolute():
        schema_path = workspace / schema_path
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(projection: dict[str, Any]) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any]]:
        catalog_path = Path(str(projection.get("catalog_path") or ""))
        catalog = _read_json(catalog_path)
        session_id = str(catalog.get("session_id") or "")
        cache_path = catalog_path.parent / "scope_binding_proposals.json"
        cached = _read_json(cache_path)
        if isinstance(cached.get("output"), dict):
            normalized_cached_output = _normalize_plan_proposals(cached["output"])
            if normalized_cached_output != cached["output"]:
                cached["output"] = normalized_cached_output
                _write_json(cache_path, cached)
        all_work_items = [
            item
            for item in catalog.get("work_items") or []
            if isinstance(item, dict)
        ]
        missing_work_items = [item for item in all_work_items if not item.get("plan_ids")]
        if not missing_work_items:
            return session_id, {}, {"会话": session_id, "状态": "无需调用", "候选数": 0}
        prompt_version = str(
            service.settings.get("binding_prompt_version") or "scope-binding-candidates-v1"
        )
        input_sha = hashlib.sha256(
            json.dumps(
                {
                    "prompt_version": prompt_version,
                    "catalog_sha256": _file_sha256(catalog_path),
                    "plans_sha256": _plan_authority_digest(plan_catalog),
                    "missing_work_item_ids": [
                        str(item.get("work_item_id") or "") for item in missing_work_items
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if cached.get("input_sha256") == input_sha and cached.get("status") == "complete":
            output = _normalize_plan_proposals(cached.get("output") or {})
            if output != cached.get("output"):
                cached["output"] = output
                _write_json(cache_path, cached)
            return session_id, _proposal_map(output), {
                "会话": session_id,
                "状态": "已复用",
                "候选数": sum(1 for row in output.get("proposals") or [] if row.get("state") == "candidate"),
            }
        prompt = _binding_agent_prompt(
            catalog_path=catalog_path,
            plans_root=workspace / "docs" / "plans",
            session_id=session_id,
            prompt_version=prompt_version,
        )
        async with semaphore:
            result = await service._invoke(
                prompt=prompt,
                schema_path=schema_path,
                run_id=f"scope-binding-{session_id}-{uuid.uuid4().hex}",
            )
        attempts = [_run_record(result)]
        output = _structured_payload(result)
        if output is None:
            return session_id, {}, {"会话": session_id, "状态": "失败", "原因": "Agent 未返回结构化对象"}
        if not agent_tool_calls(result):
            return session_id, {}, {"会话": session_id, "状态": "失败", "原因": "Agent 未使用只读工具检索目录或 Plan 真源"}
        output = _normalize_plan_proposals(output)
        _validate_plan_proposals(
            output,
            session_id=session_id,
            work_items=missing_work_items,
            plan_catalog=plan_catalog,
        )
        _write_json(
            cache_path,
            {
                "status": "complete",
                "status_zh": "完成",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_sha256": input_sha,
                "prompt_version": prompt_version,
                "model": service.settings["model"],
                "semantic_mode": "Codex Agent自主读取会话目录与Plan真源",
                "output": output,
                "attempts": attempts,
            },
        )
        return session_id, _proposal_map(output), {
            "会话": session_id,
            "状态": "完成",
            "候选数": sum(1 for row in output.get("proposals") or [] if row.get("state") == "candidate"),
        }

    results = await asyncio.gather(
        *(one(row) for row in batch.get("session_projections") or []),
        return_exceptions=True,
    )
    suggestions: dict[str, dict[str, dict[str, Any]]] = {}
    audit: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            audit.append({"会话": "未知", "状态": "失败", "原因": str(result)})
            continue
        session_id, rows, record = result
        suggestions[session_id] = rows
        audit.append(record)
    return suggestions, {
        "状态": "完成" if all(row["状态"] != "失败" for row in audit) else "部分完成",
        "模型": service.settings["model"],
        "会话": audit,
        "模型调用": sum(1 for row in audit if row["状态"] == "完成"),
        "复用": sum(1 for row in audit if row["状态"] == "已复用"),
        "Plan候选": sum(int(row.get("候选数") or 0) for row in audit),
    }


def _binding_agent_prompt(
    *,
    catalog_path: Path,
    plans_root: Path,
    session_id: str,
    prompt_version: str,
) -> str:
    return f"""你是 AI 长期学习中心的只读 Plan 归属 Agent。

权威入口：
- 完整会话工作目录：{catalog_path.resolve()}
- 全部 Plan 真源目录：{plans_root.resolve()}
- session_id：{session_id}
- prompt_version：{prompt_version}

请使用只读工具自行读取完整 catalog.json。Windows 上读取 UTF-8 文本时显式使用 `Get-Content -Encoding utf8`，避免中文乱码。找出 plan_ids 为空的全部工作项，再按其 Project 范围自行搜索和读取相关 plan.md 的完整内容；不要等待外层提供候选摘要或把工作项打成 prompt 数据包。需要辨别时可以继续读取 Plan 引用的工程文件与决策真源。

读取、引用、盘点或评价某个 Plan 不等于工作在推进它。只有工作目标、实际动作和 Plan 范围确实一致时才给 candidate；不清楚就给 unknown 和空 plan_id。你无权给 confirmed。plan_id 必须来自 docs/plans 下真实存在且与该工作项同 Project 的目录。每个 plan_ids 为空的工作项恰好输出一条 proposal，并且 evidence_refs 只能引用该工作项已有证据。所有 rationale 使用清楚、具体的中文。schema_version 固定为 scope-binding-proposals-v1，session_id 原样返回。

完成检索和逐项自检后提交符合输出 schema 的结构化对象。"""


def _validate_plan_proposals(
    output: dict[str, Any],
    *,
    session_id: str,
    work_items: list[dict[str, Any]],
    plan_catalog: dict[str, dict[str, str]],
) -> None:
    if str(output.get("schema_version") or "") != "scope-binding-proposals-v1":
        raise ValueError("Plan 候选输出 schema_version 不匹配")
    if str(output.get("session_id") or "") != session_id:
        raise ValueError("Plan 候选输出 session_id 不匹配")
    items = {str(row["work_item_id"]): row for row in work_items}
    proposals = output.get("proposals") or []
    if {str(row.get("work_item_id") or "") for row in proposals} != set(items):
        raise ValueError("Plan 候选输出未逐项覆盖工作项")
    for row in proposals:
        work_item_id = str(row.get("work_item_id") or "")
        plan_id = str(row.get("plan_id") or "")
        state = str(row.get("state") or "")
        allowed_projects = {
            str(project_id)
            for project_id in items[work_item_id].get("project_ids") or []
            if project_id
        }
        plan_meta = plan_catalog.get(plan_id) or {}
        if state == "candidate" and (
            plan_id not in plan_catalog
            or not allowed_projects
            or str(plan_meta.get("project") or "") not in allowed_projects
        ):
            raise ValueError(f"Plan 候选越界或跨 Project：{plan_id}")
        if state == "unknown" and plan_id:
            raise ValueError("unknown 状态不得携带 plan_id")
        allowed_refs = set(items[work_item_id].get("evidence_refs") or [])
        if not set(row.get("evidence_refs") or []).issubset(allowed_refs):
            raise ValueError(f"Plan 候选证据越界：{work_item_id}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plan_authority_digest(plan_catalog: dict[str, dict[str, str]]) -> str:
    rows = []
    for plan_id, meta in sorted(plan_catalog.items()):
        path = Path(str(meta.get("path") or ""))
        rows.append(
            {
                "plan_id": plan_id,
                "path": str(path),
                "sha256": _file_sha256(path) if path.is_file() else "missing",
            }
        )
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_plan_proposals(output: dict[str, Any]) -> dict[str, Any]:
    """Drop model-added schema metadata that is not part of the data contract."""

    normalized = dict(output)
    normalized.pop("$schema", None)
    return normalized


def _proposal_map(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("work_item_id") or ""): dict(row)
        for row in output.get("proposals") or []
        if isinstance(row, dict) and row.get("work_item_id")
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = [
    "backfill_batch_bindings",
    "backfill_batch_bindings_with_semantic_plans",
    "latest_batch_manifest",
]
