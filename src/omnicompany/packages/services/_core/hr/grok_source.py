# [OMNI] material_id="material:core.hr.grok-source.py" origin=codex ts=2026-08-18T00:00:00+08:00 type=module status=active agent=codex
# [OMNI] summary="Stream Grok chat histories into attributable HR write and command events"
# [OMNI] tags=hr,grok,attribution,provenance,watermark
"""Incremental collector for ``~/.grok/sessions``.

Grok keeps tool arguments in ``chat_history.jsonl`` and exact completion time /
outcome in the sibling ``events.jsonl``.  The tool-call id joins those two
sources without copying command bodies or file contents into the HR ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._jsonl_util import FileWatermark, JsonlTail
from .schema import CollectResult, HrEvent, validate_event


WRITE_TOOLS = {"write", "search_replace"}
COMMAND_TOOLS = {"run_terminal_command"}
_TRACKED_TOOLS = WRITE_TOOLS | COMMAND_TOOLS
_MAX_FAILURE_SAMPLES = 20


def _read_summary(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _normalize_target(raw: Any, cwd: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    value = Path(raw.strip())
    if not value.is_absolute() and cwd:
        value = Path(cwd) / value
    return str(value)


def _completion_index(path: Path) -> tuple[dict[str, dict[str, Any]], int, str]:
    """Return tool completions keyed by the durable Grok tool-call id."""
    rows: dict[str, dict[str, Any]] = {}
    lines = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line_number, raw in enumerate(stream, 1):
                lines += 1
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict) or value.get("type") != "tool_completed":
                    continue
                call_id = str(value.get("tool_call_id") or "")
                if call_id:
                    rows[call_id] = {
                        "ts": str(value.get("ts") or ""),
                        "outcome": str(value.get("outcome") or ""),
                        "tool_name": str(value.get("tool_name") or ""),
                        "duration_ms": value.get("duration_ms"),
                        "line_number": line_number,
                    }
    except OSError as exc:
        return {}, lines, str(exc)
    return rows, lines, ""


def collect_grok_sessions(
    root: Path, *, watermark_state: dict[str, Any] | None = None
) -> CollectResult:
    """Collect Grok write/command calls from ``<root>/*/*/chat_history.jsonl``."""
    root = Path(root)
    watermark_state = watermark_state or {}
    new_watermark: dict[str, Any] = dict(watermark_state)
    result = CollectResult(provider="grok", watermark=new_watermark)

    for history_path in sorted(root.glob("*/*/chat_history.jsonl")):
        key = str(history_path)
        result.files_seen += 1
        old_state = watermark_state.get(key)
        old_state = old_state if isinstance(old_state, dict) else {}
        tail = JsonlTail(history_path, FileWatermark.from_dict(old_state))

        summary = _read_summary(history_path.with_name("summary.json"))
        info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
        session_id = str(info.get("id") or history_path.parent.name)
        cwd = str(info.get("cwd") or "")
        current_model = str(summary.get("current_model_id") or "")
        parent_session_id = str(summary.get("parent_session_id") or "")
        fallback_ts = str(summary.get("created_at") or summary.get("updated_at") or "")
        completions: dict[str, dict[str, Any]] | None = None

        for line_number, row in tail:
            if row.get("type") != "assistant":
                continue
            row_model = str(row.get("model_id") or "").strip()
            if row_model:
                current_model = row_model
            tool_calls = row.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool = str(tool_call.get("name") or "")
                if tool not in _TRACKED_TOOLS:
                    continue
                if completions is None:
                    completions, completion_lines, completion_error = _completion_index(
                        history_path.with_name("events.jsonl")
                    )
                    result.lines_scanned += completion_lines
                    if completion_error and len(result.failures) < _MAX_FAILURE_SAMPLES:
                        result.failures.append(
                            f"{history_path.with_name('events.jsonl')}: read failed: {completion_error}"
                        )
                call_id = str(tool_call.get("id") or "")
                args = _parse_arguments(tool_call.get("arguments"))
                kind = "write" if tool in WRITE_TOOLS else "command"
                raw_target = (
                    args.get("file_path")
                    or args.get("target_file")
                    or args.get("notebook_path")
                    or args.get("path")
                    or ""
                )
                target = _normalize_target(raw_target, cwd) if kind == "write" else ""
                if kind == "write" and not target:
                    if len(result.failures) < _MAX_FAILURE_SAMPLES:
                        result.failures.append(
                            f"write-without-target: {key}#L{line_number} (tool_call_id={call_id})"
                        )
                    continue
                completion = (completions or {}).get(call_id, {})
                outcome = str(completion.get("outcome") or "")
                verdict = "applied" if outcome == "success" else "error" if outcome == "error" else "unknown"
                completion_line = completion.get("line_number")
                note_parts = []
                if completion_line:
                    note_parts.append(f"completion@events.jsonl#L{completion_line}")
                if completion.get("duration_ms") is not None:
                    note_parts.append(f"duration_ms={completion['duration_ms']}")
                event = HrEvent(
                    ts=str(completion.get("ts") or fallback_ts),
                    provider="grok",
                    session_id=session_id,
                    kind=kind,
                    evidence_ref=f"{history_path.resolve(strict=False)}#L{line_number}",
                    operation_id=call_id,
                    model=current_model,
                    model_confidence="high" if current_model else "unknown",
                    parent_session_id=parent_session_id,
                    is_subagent=bool(parent_session_id),
                    cwd=cwd,
                    tool=tool,
                    target=target,
                    target_confidence="direct" if kind == "write" else "none",
                    verdict=verdict,
                    note="; ".join(note_parts),
                )
                problems = validate_event(event)
                if problems:
                    if len(result.failures) < _MAX_FAILURE_SAMPLES:
                        result.failures.append(f"{key}#L{line_number}: invalid event {problems}")
                    continue
                result.events.append(event)

        new_watermark[key] = tail.watermark.to_dict()
        result.lines_scanned += tail.lines_read
        if tail.error is not None:
            result.files_failed += 1
            if len(result.failures) < _MAX_FAILURE_SAMPLES:
                result.failures.append(f"{key}: read failed: {tail.error}")
        elif tail.is_empty:
            result.files_empty += 1
        else:
            result.files_parsed += 1
            if tail.skipped and len(result.failures) < _MAX_FAILURE_SAMPLES:
                result.failures.append(f"{key}: {tail.skipped} bad json line(s) skipped")

    result.watermark = new_watermark
    return result


__all__ = ["COMMAND_TOOLS", "WRITE_TOOLS", "collect_grok_sessions"]
