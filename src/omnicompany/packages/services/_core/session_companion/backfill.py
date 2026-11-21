# [OMNI] origin=codex domain=services/session-companion ts=2026-08-14 type=service
# [OMNI] material_id="material:services.session-companion.backfill.py"
"""Controlled historical enqueue planning for companion lifecycle gaps."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from omnicompany.dashboard.ccdaemon.api import (
    CLI_AGENT_PROVIDER_IDS,
    canonical_cli_provider_id,
)
from omnicompany.packages.services.boss_sight.services.project_resolution import (
    canonical_project_id,
    project_ids_referenced_in_text,
)

from .audit import audit_record
from .coverage import runtime_session_registry
from .service import enqueue_lifecycle_event
from .store import CompanionStore

TranscriptLoader = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


def _scope_present(value: Mapping[str, Any]) -> bool:
    return any(
        str(value.get(field) or "").strip()
        for field in ("project", "workstream", "active_plan", "task_id")
    )


def _binding_confirmed(value: Mapping[str, Any]) -> bool:
    try:
        from omnicompany.packages.services._core.identity import (
            session_binding_is_confirmed,
        )

        return session_binding_is_confirmed(dict(value))
    except Exception:  # noqa: BLE001 - absence lowers authority; never guesses
        return False


def _binding_for_session(
    session_key: str,
    entry: Mapping[str, Any],
    bindings: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    provider = canonical_cli_provider_id(entry.get("provider"))
    native_id = str(
        entry.get("provider_session_id") or entry.get("claude_session_id") or ""
    ).strip()
    direct_keys = [session_key, f"pty_{session_key}"]
    if native_id:
        direct_keys.extend((f"{provider}_{native_id}", f"cc_{native_id}", native_id))
    for key in direct_keys:
        value = bindings.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    for value in bindings.values():
        if not isinstance(value, Mapping):
            continue
        if str(value.get("pty_id") or "") == session_key:
            return dict(value)
        if native_id and native_id in {
            str(value.get("session_id") or ""),
            str(value.get("claude_session_id") or ""),
        }:
            return dict(value)
    return {}


def load_transcript_preview(
    session_key: str,
    _entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse the cross-provider locator and preview parser as the transcript truth."""

    try:
        from omnicompany.packages.services._core.identity import locate_sessions

        matches = locate_sessions(
            session_key,
            limit=1,
            peek=True,
            include_orphans=False,
        )
    except Exception:  # noqa: BLE001 - one corrupt provider transcript is a skip
        return {}
    if not matches:
        return {}
    match = matches[0]
    preview = dict(match.preview or {})
    transcript = match.transcript
    if transcript is not None:
        preview["transcript_path"] = transcript.path
        preview["transcript_mtime"] = float(transcript.mtime or 0.0)
    return preview


def _record_covers_session(
    record: Mapping[str, Any],
    *,
    provider: str,
    trace_id: str,
    session_id: str,
) -> bool:
    """Return whether a terminal event is usable coverage evidence.

    A queued/done companion event is not coverage merely because its identity
    matches.  Historical PTY sessions can produce a sealed but degraded event
    with an empty provider output; treating that event as covered permanently
    suppresses the only recovery path.  The event payload is the evidence
    boundary: a generated placeholder in ``proposal.digest`` cannot replace
    missing assistant/summary text from the source session.
    """

    event = record.get("event") if isinstance(record.get("event"), Mapping) else {}
    event_provider = canonical_cli_provider_id(event.get("provider"), default="unknown")
    event_trace = str(event.get("trace_id") or "")
    event_session = str(event.get("session_id") or "")
    if event_provider != provider:
        return False
    if trace_id and event_trace and event_trace != trace_id:
        return False
    if session_id and event_session and event_session != session_id:
        return False

    audit = record.get("audit") if isinstance(record.get("audit"), Mapping) else None
    if audit is None:
        audit = audit_record(record)
    if str(audit.get("status") or "") != "healthy":
        return False

    return any(
        str(event.get(field) or "").strip()
        for field in ("assistant_output", "compact_summary")
    )


def _record_is_pending(
    record: Mapping[str, Any],
    *,
    provider: str,
    trace_id: str,
    session_id: str,
) -> bool:
    """Suppress duplicate enqueue while an otherwise valid event is in flight."""

    if str(record.get("state") or "") not in {"queued", "running"}:
        return False
    event = record.get("event") if isinstance(record.get("event"), Mapping) else {}
    return (
        canonical_cli_provider_id(event.get("provider"), default="unknown") == provider
        and (not trace_id or str(event.get("trace_id") or "") == trace_id)
        and (not session_id or str(event.get("session_id") or "") == session_id)
    )


def plan_backfill(
    *,
    sessions: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, Mapping[str, Any]],
    records: Iterable[Mapping[str, Any]],
    transcript_loader: TranscriptLoader = load_transcript_preview,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Plan newest historical gaps; never mutate session or companion stores."""

    seen_traces: set[str] = set()
    seen_sessions: set[tuple[str, str]] = set()
    for record in records:
        event = record.get("event") if isinstance(record.get("event"), Mapping) else {}
        trace_id = str(event.get("trace_id") or "")
        provider = canonical_cli_provider_id(event.get("provider"), default="unknown")
        session_id = str(event.get("session_id") or "")
        covered = _record_covers_session(
            record,
            provider=provider,
            trace_id=trace_id,
            session_id=session_id,
        )
        pending = _record_is_pending(
            record,
            provider=provider,
            trace_id=trace_id,
            session_id=session_id,
        )
        if not (covered or pending):
            continue
        if trace_id:
            seen_traces.add(trace_id)
        if session_id:
            seen_sessions.add((provider, session_id))

    ordered = sorted(
        (
            (str(session_key), value)
            for session_key, value in sessions.items()
            if isinstance(value, Mapping)
        ),
        key=lambda row: float(row[1].get("started_at") or row[1].get("updated_at") or 0.0),
        reverse=True,
    )
    planned: list[dict[str, Any]] = []
    for session_key, entry in ordered:
        provider = canonical_cli_provider_id(entry.get("provider"), default="unknown")
        native_id = str(
            entry.get("provider_session_id") or entry.get("claude_session_id") or ""
        ).strip()
        event_session_id = native_id or session_key
        binding = _binding_for_session(session_key, entry, bindings)
        trace_id = str(binding.get("trace_id") or session_key).strip()
        item: dict[str, Any] = {
            "session_key": session_key,
            "provider": provider,
            "session_id": event_session_id,
            "session_identity": "native" if native_id else "pty",
            "trace_id": trace_id,
            "cwd": str(entry.get("cwd") or ""),
            "project": str(binding.get("project") or ""),
            "workstream": str(binding.get("workstream") or ""),
            "active_plan": str(binding.get("active_plan") or ""),
            "binding_scoped": _scope_present(binding),
            "binding_confirmed": _binding_confirmed(binding),
        }
        if provider not in CLI_AGENT_PROVIDER_IDS:
            item.update({"action": "skip", "reason": "non_agent_runtime"})
        elif trace_id in seen_traces or (provider, event_session_id) in seen_sessions:
            item.update({"action": "skip", "reason": "already_covered"})
        else:
            preview = dict(transcript_loader(session_key, entry) or {})
            first_user = str(preview.get("first_user") or "").strip()
            latest_user = str(preview.get("last_user") or "").strip()
            transcript_source = "native_transcript"
            if not (first_user or latest_user):
                # ``provider_title`` is the bounded exact first submitted PTY
                # prompt, not a generated summary. It is valid topic evidence
                # but never assistant/decision evidence.
                provider_title = str(entry.get("provider_title") or "").strip()
                if bool(entry.get("has_user_turn")) and provider_title:
                    first_user = provider_title
                    latest_user = provider_title
                    transcript_source = "pty_first_prompt"
            if not (first_user or latest_user):
                item.update({"action": "skip", "reason": "no_user_transcript"})
            else:
                assistant_output = str(preview.get("last_text") or "").strip()
                evidence_projects = project_ids_referenced_in_text(
                    f"{first_user}\n{latest_user}\n{assistant_output}"
                )
                bound_project = canonical_project_id(label=binding.get("project"))
                if bound_project:
                    evidence_projects.add(bound_project)
                item["project_evidence"] = sorted(evidence_projects)
                has_plan_evidence = any(
                    str(binding.get(field) or "").strip()
                    for field in ("active_plan", "task_id")
                )
                if not evidence_projects and not has_plan_evidence:
                    item.update({"action": "skip", "reason": "no_project_evidence"})
                elif len(evidence_projects) > 1 and not item["binding_confirmed"]:
                    item.update({
                        "action": "skip",
                        "reason": "multiple_project_evidence",
                    })
                else:
                    item.update({
                        "action": "enqueue",
                        "reason": "missing_companion_event",
                        "first_user_input": first_user or latest_user,
                        "user_input": latest_user or first_user,
                        "assistant_output": assistant_output,
                        "transcript_path": str(preview.get("transcript_path") or ""),
                        "transcript_mtime": float(preview.get("transcript_mtime") or 0.0),
                        "transcript_source": transcript_source,
                    })
        planned.append(item)
        if len(planned) >= max(1, limit):
            break
    return planned


def run_backfill(
    *,
    store: CompanionStore | None = None,
    sessions: Mapping[str, Mapping[str, Any]] | None = None,
    bindings: Mapping[str, Mapping[str, Any]] | None = None,
    transcript_loader: TranscriptLoader = load_transcript_preview,
    limit: int = 20,
    apply: bool = False,
    kick: bool = False,
) -> dict[str, Any]:
    """Dry-run by default; explicit apply only enqueues deterministic events."""

    from omnicompany.packages.services._core.identity import all_session_bindings

    store = store or CompanionStore()
    sessions = sessions if sessions is not None else runtime_session_registry()
    bindings = bindings if bindings is not None else all_session_bindings()
    records = [store.get(row["event_id"]) for row in store.list(limit=100_000)]
    items = plan_backfill(
        sessions=sessions,
        bindings=bindings,
        records=records,
        transcript_loader=transcript_loader,
        limit=limit,
    )
    enqueued = 0
    existing = 0
    if apply:
        for item in items:
            if item.get("action") != "enqueue":
                continue
            result = enqueue_lifecycle_event(
                trigger="manual",
                trace_id=str(item["trace_id"]),
                provider=str(item["provider"]),
                session_id=str(item["session_id"]),
                cwd=str(item.get("cwd") or ""),
                first_user_input=str(item.get("first_user_input") or ""),
                user_input=str(item.get("user_input") or ""),
                assistant_output=str(item.get("assistant_output") or ""),
                project=str(item.get("project") or ""),
                workstream=str(item.get("workstream") or ""),
                active_plan=str(item.get("active_plan") or ""),
                transcript_mtime=float(item.get("transcript_mtime") or 0.0),
                generation=(
                    f"backfill:{item['provider']}:{item['session_id']}:"
                    f"{float(item.get('transcript_mtime') or 0.0):.6f}"
                ),
                store=store,
                dry_run=False,
                kick=kick,
            )
            item["event_id"] = result.get("event_id")
            item["result"] = "enqueued" if result.get("created") else "existing"
            enqueued += int(bool(result.get("created")))
            existing += int(not bool(result.get("created")))
    return {
        "mode": "apply" if apply else "dry_run",
        "items": items,
        "count": len(items),
        "enqueue_candidates": sum(item.get("action") == "enqueue" for item in items),
        "enqueued": enqueued,
        "existing": existing,
        "store_root": str(Path(store.root)),
    }


__all__ = [
    "load_transcript_preview",
    "plan_backfill",
    "run_backfill",
]
