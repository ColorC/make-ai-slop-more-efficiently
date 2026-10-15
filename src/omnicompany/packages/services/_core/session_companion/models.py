# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.models.py"
"""Versioned lifecycle event envelope for companion maintenance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA_VERSION = 1
TEXT_LIMIT = 24_000
TRIGGERS = {"pre_compact", "post_compact", "stop", "native_compact", "manual", "user_prompt"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, *, limit: int = TEXT_LIMIT) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    # PTY providers can leak lone UTF-16 surrogates when a redraw is split at
    # the Windows console boundary.  Keep the surrounding text but make the
    # value valid UTF-8 before it reaches the durable queue.
    text = "".join("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char for char in text).strip()
    if len(text) <= limit:
        return text
    half = max(1, (limit - 80) // 2)
    return f"{text[:half]}\n…[{len(text) - half * 2} chars omitted]…\n{text[-half:]}"


def stable_digest(*values: Any, size: int = 24) -> str:
    body = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    body = "".join("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char for char in body)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:size]


@dataclass(frozen=True)
class CompanionEvent:
    """Immutable input captured by a main-session lifecycle hook."""

    event_id: str
    trace_id: str
    provider: str
    session_id: str
    cwd: str
    trigger: str
    first_user_input: str = ""
    user_input: str = ""
    assistant_output: str = ""
    compact_summary: str = ""
    project: str = ""
    workstream: str = ""
    active_plan: str = ""
    close_reason: str = ""
    close_actor: str = ""
    close_evidence: dict[str, Any] = field(default_factory=dict)
    lane_key: str = ""
    evidence_refs: tuple[str, ...] = ()
    transcript_mtime: float = 0.0
    created_at: str = field(default_factory=_now_iso)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_refs"] = list(self.evidence_refs)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompanionEvent":
        trigger = str(value.get("trigger") or "manual")
        if trigger not in TRIGGERS:
            raise ValueError(f"unsupported companion trigger: {trigger}")
        return cls(
            event_id=str(value["event_id"]),
            trace_id=str(value["trace_id"]),
            provider=str(value.get("provider") or "unknown"),
            session_id=str(value.get("session_id") or ""),
            cwd=str(value.get("cwd") or ""),
            trigger=trigger,
            first_user_input=_text(value.get("first_user_input")),
            user_input=_text(value.get("user_input")),
            assistant_output=_text(value.get("assistant_output")),
            compact_summary=_text(value.get("compact_summary")),
            project=str(value.get("project") or ""),
            workstream=str(value.get("workstream") or ""),
            active_plan=str(value.get("active_plan") or ""),
            close_reason=_text(value.get("close_reason")),
            close_actor=_text(value.get("close_actor")),
            close_evidence=dict(value.get("close_evidence") or {}),
            lane_key=str(value.get("lane_key") or ""),
            evidence_refs=tuple(str(ref) for ref in value.get("evidence_refs") or [] if ref),
            transcript_mtime=float(value.get("transcript_mtime") or 0.0),
            created_at=str(value.get("created_at") or _now_iso()),
            schema_version=int(value.get("schema_version") or SCHEMA_VERSION),
        )


def build_event(
    *,
    trace_id: str,
    provider: str,
    session_id: str,
    cwd: str,
    trigger: str,
    first_user_input: str = "",
    user_input: str = "",
    assistant_output: str = "",
    compact_summary: str = "",
    project: str = "",
    workstream: str = "",
    active_plan: str = "",
    close_reason: str = "",
    close_actor: str = "",
    close_evidence: Mapping[str, Any] | None = None,
    transcript_mtime: float = 0.0,
    generation: str = "",
) -> CompanionEvent:
    """Build a deterministic event id so overlapping compact/Stop hooks dedupe."""

    if not trace_id:
        raise ValueError("trace_id is required")
    if trigger not in TRIGGERS:
        raise ValueError(f"unsupported companion trigger: {trigger}")
    user_text = _text(user_input)
    assistant_text = _text(assistant_output)
    summary_text = _text(compact_summary)
    content_digest = stable_digest(user_text, assistant_text, summary_text)
    generation_key = generation.strip() or content_digest
    event_id = f"evt_{stable_digest(trace_id, trigger, generation_key)}"
    lane_scope = project.strip() or active_plan.strip() or "unfiled"
    lane_key = f"{trace_id}:{lane_scope}"
    evidence_ref = f"companion://{trace_id}/{event_id}"
    return CompanionEvent(
        event_id=event_id,
        trace_id=trace_id,
        provider=provider.strip() or "unknown",
        session_id=session_id.strip(),
        cwd=cwd.strip(),
        trigger=trigger,
        first_user_input=_text(first_user_input),
        user_input=user_text,
        assistant_output=assistant_text,
        compact_summary=summary_text,
        project=project.strip(),
        workstream=workstream.strip(),
        active_plan=active_plan.strip(),
        close_reason=_text(close_reason),
        close_actor=_text(close_actor),
        close_evidence=dict(close_evidence or {}),
        lane_key=lane_key,
        evidence_refs=(evidence_ref,),
        transcript_mtime=max(0.0, float(transcript_mtime or 0.0)),
    )


__all__ = ["CompanionEvent", "SCHEMA_VERSION", "TRIGGERS", "build_event", "stable_digest"]
