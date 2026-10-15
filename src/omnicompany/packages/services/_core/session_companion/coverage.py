# [OMNI] origin=codex domain=services/session-companion ts=2026-08-14 type=service
# [OMNI] material_id="material:services.session-companion.coverage.py"
"""Deterministic provider/session coverage for the companion lifecycle."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from omnicompany.dashboard.ccdaemon.api import (
    CLI_AGENT_PROVIDER_IDS,
    canonical_cli_provider_id,
)


def runtime_session_registry() -> dict[str, dict[str, Any]]:
    """Read the Dashboard session registry without creating another truth source."""

    try:
        from omnicompany.dashboard.ccdaemon.api import read_meta_store

        values = read_meta_store(use_cache=False)
    except Exception:  # noqa: BLE001 - coverage remains queryable during daemon degradation
        return {}
    return {
        str(session_id): dict(value)
        for session_id, value in values.items()
        if isinstance(value, Mapping)
    }


def provider_coverage_report(
    records: Iterable[Mapping[str, Any]],
    *,
    bindings: Mapping[str, Mapping[str, Any]] | None = None,
    sessions: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare registered runtimes with sessions, events, bindings and skips."""

    bindings = bindings or {}
    sessions = sessions or {}
    session_counts: Counter[str] = Counter()
    ignored_session_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    event_traces: dict[str, set[str]] = defaultdict(set)
    terminal_counts: Counter[str] = Counter()
    scoped_bindings: Counter[str] = Counter()
    binding_counts: Counter[str] = Counter()
    skip_reasons: dict[str, Counter[str]] = defaultdict(Counter)

    for value in sessions.values():
        provider = canonical_cli_provider_id(value.get("provider"), default="unknown")
        if provider in CLI_AGENT_PROVIDER_IDS:
            session_counts[provider] += 1
        else:
            ignored_session_counts[provider] += 1

    record_count = 0
    for record in records:
        record_count += 1
        event = record.get("event") if isinstance(record.get("event"), Mapping) else {}
        provider = canonical_cli_provider_id(event.get("provider"), default="unknown")
        event_counts[provider] += 1
        trace_id = str(event.get("trace_id") or "")
        if trace_id:
            event_traces[provider].add(trace_id)
        if str(record.get("state") or "") in {"done", "failed"}:
            terminal_counts[provider] += 1
        for receipt in record.get("receipts") or []:
            if not isinstance(receipt, Mapping):
                continue
            action = str(receipt.get("action") or "unknown")
            status = str(receipt.get("status") or "unknown")
            if status == "skipped" or action == "unresolved":
                reason = str(
                    receipt.get("reason")
                    or receipt.get("message")
                    or f"{action}:{status}"
                ).strip()
                skip_reasons[provider][reason[:300]] += 1

    scope_fields = ("project", "workstream", "active_plan", "task_id")
    for value in bindings.values():
        provider = canonical_cli_provider_id(value.get("provider"), default="unknown")
        binding_counts[provider] += 1
        if any(str(value.get(field) or "").strip() for field in scope_fields):
            scoped_bindings[provider] += 1

    matrix: list[dict[str, Any]] = []
    for provider in sorted(CLI_AGENT_PROVIDER_IDS):
        registered = session_counts[provider]
        events = event_counts[provider]
        if events:
            status = "covered"
        elif registered:
            status = "missing_events"
        else:
            status = "no_sessions"
        matrix.append({
            "provider": provider,
            "status": status,
            "registered_sessions": registered,
            "companion_events": events,
            "terminal_events": terminal_counts[provider],
            "distinct_traces": len(event_traces[provider]),
            "bindings": binding_counts[provider],
            "scoped_bindings": scoped_bindings[provider],
            "skip_reasons": dict(skip_reasons[provider].most_common()),
        })

    unknown_event_providers = {
        provider: count
        for provider, count in sorted(event_counts.items())
        if provider not in CLI_AGENT_PROVIDER_IDS
    }
    return {
        "providers": matrix,
        "totals": {
            "registered_provider_count": len(CLI_AGENT_PROVIDER_IDS),
            "registered_sessions": sum(session_counts.values()),
            "companion_records": record_count,
            "bindings": len(bindings),
            "scoped_bindings": sum(scoped_bindings.values()),
            "providers_covered": sum(row["status"] == "covered" for row in matrix),
            "providers_missing_events": sum(row["status"] == "missing_events" for row in matrix),
        },
        "ignored_session_providers": dict(sorted(ignored_session_counts.items())),
        "unknown_event_providers": unknown_event_providers,
    }


__all__ = ["provider_coverage_report", "runtime_session_registry"]
