# [OMNI] origin=codex domain=dashboard/boss_sight/services ts=2026-08-12 type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.session_lineage.py"
"""Normalize provider-specific session ancestry for Dashboard consumers.

The parent relationship already exists in provider transcripts or the identity
binding ledger.  This module only projects it; it does not create another
session registry.
"""

from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_TEAMMATE_SUMMARY = re.compile(r"<teammate-message\b[^>]*\bsummary=(['\"])(.*?)\1", re.IGNORECASE)
_VOID_TEXT = {"", "(无)", "无", "信息不足", "暂无信息", "进行中…"}


def _compact_label(value: Any, limit: int = 140) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split()).strip(" ·-—")
    return "" if text in _VOID_TEXT else text[:limit]


def _claude_subagent_lineage(path: Path) -> dict[str, Any]:
    parts = [part.casefold() for part in path.parts]
    if "subagents" not in parts:
        return {}
    index = parts.index("subagents")
    parent_id = path.parts[index - 1] if index > 0 else ""
    agent_name = path.stem.removeprefix("agent-")
    task = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(16):
                line = handle.readline(1_048_576)
                if not line:
                    break
                try:
                    row = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                parent_id = str(row.get("sessionId") or parent_id)
                agent_name = str(row.get("agentId") or agent_name)
                message = row.get("message") if isinstance(row.get("message"), dict) else {}
                content = message.get("content")
                if isinstance(content, str):
                    match = _TEAMMATE_SUMMARY.search(content)
                    task = _compact_label(match.group(2) if match else content)
                if parent_id and task:
                    break
    except OSError:
        pass
    return {
        "kind": "derived",
        "origin": "ai",
        "relation": "subagent",
        "parent_session_id": parent_id,
        "depth": 1,
        "agent_name": agent_name,
        "task": task,
    }


def _codex_lineage(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            line = handle.readline(1_048_576)
        row = json.loads(line)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(row, dict) or row.get("type") != "session_meta":
        return {}
    meta = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
    subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
    spawn = subagent.get("thread_spawn") if isinstance(subagent.get("thread_spawn"), dict) else {}
    if str(meta.get("thread_source") or "").casefold() != "subagent" and not subagent:
        return {"kind": "root", "origin": "human", "relation": "created"}
    agent_path = str(spawn.get("agent_path") or "")
    task = _compact_label(agent_path.rsplit("/", 1)[-1].replace("_", " ").replace("-", " "))
    try:
        depth = max(1, int(spawn.get("depth") or 1))
    except (TypeError, ValueError):
        depth = 1
    return {
        "kind": "derived",
        "origin": "ai",
        "relation": "subagent",
        "parent_session_id": str(spawn.get("parent_thread_id") or ""),
        "depth": depth,
        "agent_name": str(spawn.get("agent_nickname") or ""),
        "agent_path": agent_path,
        "task": task,
    }


@lru_cache(maxsize=1024)
def _transcript_lineage(file_path: str) -> dict[str, Any]:
    if not file_path:
        return {}
    path = Path(file_path)
    if path.suffix.casefold() != ".jsonl":
        return {}
    if "subagents" in {part.casefold() for part in path.parts}:
        return _claude_subagent_lineage(path)
    if path.name.casefold().startswith("rollout-"):
        return _codex_lineage(path)
    return {}


def infer_session_lineage(
    record: dict[str, Any],
    *,
    binding: dict[str, Any] | None = None,
    managed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact, provider-neutral lineage projection."""

    binding = binding or {}
    managed = managed or {}
    transcript = _transcript_lineage(str(record.get("file") or ""))
    parent_id = str(binding.get("parent_agent_id") or "")
    fork_id = str(binding.get("fork_from_provider_session_id") or "")
    declared_origin = str(
        binding.get("session_origin")
        or record.get("origin")
        or managed.get("origin")
        or ""
    ).casefold()
    task = _compact_label(binding.get("topic"))

    if parent_id:
        lineage = {
            "kind": "derived",
            "origin": "ai",
            "relation": "worker",
            "parent_session_id": parent_id,
            "depth": 1,
            "task": task,
        }
    elif fork_id:
        lineage = {
            "kind": "derived",
            "origin": "ai" if declared_origin == "agent" else "human",
            "relation": "fork",
            "parent_session_id": fork_id,
            "depth": 1,
            "task": task,
        }
    elif transcript:
        lineage = dict(transcript)
    else:
        lineage = {
            "kind": "root",
            "origin": (
                "ai" if declared_origin == "agent"
                else "human" if declared_origin == "human"
                else "unknown"
            ),
            "relation": "created" if declared_origin else "unknown",
        }

    if transcript.get("kind") == "derived":
        for field, value in transcript.items():
            if value not in (None, "", 0):
                lineage[field] = value
    if task:
        lineage["task"] = task
    return lineage


def attach_parent_titles(records: list[dict[str, Any]]) -> None:
    """Resolve a human-readable parent label within the already-loaded slice."""

    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        for value in (record.get("session_id"), record.get("pty_id"), record.get("key")):
            if value:
                by_id[str(value)] = record
    for record in records:
        lineage = record.get("lineage")
        if not isinstance(lineage, dict) or lineage.get("kind") != "derived":
            continue
        parent_id = str(lineage.get("parent_session_id") or "")
        parent = by_id.get(parent_id)
        if parent is None and parent_id in {"boss-sight-controller", "controller:main"}:
            lineage["parent_title"] = "总控"
            continue
        if parent is None:
            continue
        label = ""
        for field in ("topic", "title", "current_task", "name"):
            label = _compact_label(parent.get(field))
            if label:
                break
        if label:
            lineage["parent_title"] = label


__all__ = ["attach_parent_titles", "infer_session_lineage"]
