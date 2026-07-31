# [OMNI] origin=codex domain=services/agent ts=2026-05-20 type=infrastructure
"""SQLite trace mirror for CLI-launched external workers.

Dashboard Claude hooks write IDE session events to ``data/ide_events.db``.
``omni worker run`` is not an IDE session, so it mirrors auditable worker
events to ``data/events.db`` with the worker run id as the default trace id.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from omnicompany.core.config import resolve_unified_db_path
from omnicompany.packages.services._core.agent.event_bridge import publish_agent_event_sync

from .base import ExternalAgentEvent, ExternalAgentRunSpec

logger = logging.getLogger(__name__)

class ExternalWorkerTraceMirror:
    """Best-effort DB mirror for provider events that matter during review."""

    def __init__(self, spec: ExternalAgentRunSpec, *, basename: str = "events.db"):
        self.spec = spec
        self.trace_id = spec.trace_id or spec.run_id
        self.basename = basename
        self.db_path = resolve_unified_db_path(basename)
        self._tool_parent_by_id: dict[str, str] = {}
        self._tool_name_by_id: dict[str, str] = {}

    def emit(self, event_type: str, payload: dict[str, Any], *, parent_id: str | None = None, tags: list[str] | None = None) -> str | None:
        published = publish_agent_event_sync(
            trace_id=self.trace_id,
            parent_id=parent_id,
            event_type=event_type,
            source=f"agent.external.{self.spec.provider}",
            payload=_json_safe(payload),
            tags=[
                "external_worker",
                f"provider:{self.spec.provider}",
                f"run:{self.spec.run_id}",
                *(tags or []),
            ],
            basename=self.basename,
        )
        if published is None:
            logger.warning("external worker trace mirror failed to publish %s", event_type)
            return None
        self.db_path = published.db_path
        return published.event_id

    def emit_started(self) -> None:
        payload = self.spec.audit_payload()
        payload["trace_db"] = str(self.db_path)
        self.emit("external_agent.started", payload)

    def emit_completed(self, *, status: str, error: str, changed_files: list[str], diff_summary: str, duration_ms: float, raw: dict[str, Any]) -> None:
        self.emit(
            "external_agent.completed",
            {
                "run_id": self.spec.run_id,
                "provider": self.spec.provider,
                "status": status,
                "error": error,
                "changed_files": changed_files,
                "diff_summary": _summarize_result(diff_summary),
                "duration_ms": duration_ms,
                "watch_paths": raw.get("watch_paths", []),
                "watched_path_changes": raw.get("watched_path_changes"),
                "trace_db": str(self.db_path),
            },
        )

    def mirror_claude_sdk_event(self, event: ExternalAgentEvent) -> None:
        for block in _iter_content_blocks(event.payload.get("content")):
            block_type = str(block.get("type") or block.get("kind") or "").strip()
            if block_type == "tool_use" or _looks_like_tool_use(block):
                self._emit_tool_call(block)
            elif block_type == "tool_result" or _looks_like_tool_result(block):
                self._emit_tool_result(block)

    def _emit_tool_call(self, block: dict[str, Any]) -> None:
        tool_use_id = str(block.get("id") or block.get("tool_use_id") or block.get("toolUseId") or "")
        tool_name = str(block.get("name") or block.get("tool_name") or block.get("toolName") or "?")
        event_id = self.emit(
            "agent.tool.call",
            {
                "tool": tool_name,
                "args": _summarize_args(block.get("input") or block.get("tool_input") or {}),
                "tool_use_id": tool_use_id or None,
                "run_id": self.spec.run_id,
                "provider": self.spec.provider,
            },
            tags=[f"tool:{tool_name}"],
        )
        if tool_use_id:
            if event_id:
                self._tool_parent_by_id[tool_use_id] = event_id
            self._tool_name_by_id[tool_use_id] = tool_name

    def _emit_tool_result(self, block: dict[str, Any]) -> None:
        tool_use_id = str(block.get("tool_use_id") or block.get("toolUseId") or block.get("id") or "")
        tool_name = self._tool_name_by_id.get(tool_use_id) or str(block.get("name") or block.get("tool_name") or "?")
        result = block.get("content", block.get("result", block.get("tool_response")))
        self.emit(
            "agent.tool.result",
            {
                "tool": tool_name,
                "result": _summarize_result(result),
                "tool_use_id": tool_use_id or None,
                "verdict": "ok" if result else "empty",
                "run_id": self.spec.run_id,
                "provider": self.spec.provider,
            },
            parent_id=self._tool_parent_by_id.get(tool_use_id),
            tags=[f"tool:{tool_name}"],
        )


def _iter_content_blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        elif hasattr(block, "model_dump"):
            dumped = block.model_dump()
            if isinstance(dumped, dict):
                blocks.append(dumped)
        elif hasattr(block, "__dict__"):
            blocks.append(dict(vars(block)))
    return blocks


def _looks_like_tool_use(block: dict[str, Any]) -> bool:
    return bool(block.get("id") and block.get("name") and "input" in block)


def _looks_like_tool_result(block: dict[str, Any]) -> bool:
    return bool(block.get("tool_use_id") or block.get("toolUseId")) and (
        "content" in block or "result" in block or "tool_response" in block
    )


def _summarize_args(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {"_raw_repr": repr(args)[:300]}
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 1500:
            out[key] = value[:1500] + f"... [truncated, total {len(value)} chars]"
        elif isinstance(value, (dict, list)):
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            out[key] = value if len(encoded) <= 1500 else encoded[:1500] + "... [truncated]"
        else:
            out[key] = value
    return out


def _summarize_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, (dict, list)):
        text = json.dumps(result, ensure_ascii=False, default=str)
    else:
        text = str(result)
    if len(text) > 4000:
        return text[:4000] + f"... [truncated, total {len(text)} chars]"
    return text


def _json_safe(data: Any) -> Any:
    try:
        json.dumps(data, ensure_ascii=False, default=str)
        return data
    except (TypeError, ValueError):
        if hasattr(data, "model_dump"):
            return data.model_dump()
        if hasattr(data, "__dict__"):
            return {key: _json_safe(value) for key, value in vars(data).items()}
        return str(data)


__all__ = ["ExternalWorkerTraceMirror"]
