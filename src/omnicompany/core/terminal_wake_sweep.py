# [OMNI] origin=claude-code domain=core ts=2026-09-06 type=infra status=active
"""Deterministic sweep that re-drives undelivered child-terminal wakes.

Background
----------
Both terminal-wake delivery paths — ``pty_routes._wake_bound_parent`` (external
PTY workers) and ``managed_agent_completion._record_quiet_terminal`` (managed
Stop/SubagentStop) — record a durable ``child_terminal`` row, ``claim_wake`` it,
then ``send_agent_input`` to the parent.  When that single delivery fails they
``release_wake`` so the row is left ``status='pending'`` with
``wake_claimed_at IS NULL``.  A terminated child never fires its Stop/PTY hook
again, so without a sweep that re-discovers the orphaned row the parent is never
woken.  ``QuietCoordinationStore.pending_terminal_wakes`` is the read-only
discovery primitive; this module is the deterministic orchestration on top of it.

Why an injected ``deliver``
---------------------------
Actually calling ``send_agent_input`` is the daemon/remote delivery behaviour a
bounded, no-remote run must not perform on its own.  So the core sweep takes the
sender as a parameter: it is exercised in tests with a fake ``deliver`` and is
scheduler-facing — a cron/heartbeat wires in the real control-plane sender via
:func:`control_plane_deliver`.  The sweep itself performs no I/O beyond the
existing store CAS primitives.

Safety
------
Every candidate is re-driven through the existing ``claim_wake`` CAS, so a
concurrent hot-path delivery is harmless: whoever claims first delivers, and the
loser's ``claim_wake`` returns ``False`` (``skipped_claimed``).  ``min_age``
keeps the sweep from racing a record the hot path is still actively delivering.
Failures schedule an exponential backoff (``defer_wake_retry``) and, past
``max_attempts``, the row is reported ``exhausted`` for alerting rather than
retried forever.  ``now`` is injected so behaviour is fully deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from omnicompany.core.quiet_coordination import QuietCoordinationStore

# Policy defaults.  min_age > 0 keeps the sweep off records the hot path is still
# delivering; the backoff geometry caps runaway retries against a down parent.
DEFAULT_MIN_AGE_SECONDS = 120.0
DEFAULT_BACKOFF_BASE_SECONDS = 300.0
DEFAULT_MAX_BACKOFF_SECONDS = 3600.0
DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_LIMIT = 100

# A manual ``omni coordination child-terminal --parent cli-...`` smoke test leaves
# a durable pending wake whose parent session never existed as a live native
# worker.  A real orphaned terminal always names a native-identity parent (a
# 16-hex worker id or provider UUID), so the ``cli-`` prefix cleanly separates
# fixture rows from deliverable ones.  These rows must stay *visible* in
# diagnostics — never silently dropped — but must never be counted as real
# deliverable work unless the parent actually resolves to a live native binding.
SYNTHETIC_FIXTURE_TARGET_PREFIX = "cli-"

# A deliver callable returns True on confirmed delivery; False (or raising)
# means the transport failed and the token must be returned for a later retry.
Deliver = Callable[[Mapping[str, Any]], bool]

# Resolves whether a wake's parent (target_session_id) is a live native binding.
# Injected so the pure sweep never performs daemon I/O on its own; a scheduler
# wires in :func:`native_live_parent_binding` and tests pass a deterministic fake.
LiveParentResolver = Callable[[Mapping[str, Any]], bool]


def is_synthetic_fixture_binding(record: Mapping[str, Any]) -> bool:
    """True when the wake's parent binding is a ``cli-*`` smoke-test placeholder.

    This is a pure, name-only classification: it flags the row as a fixture for
    the diagnostics buckets.  Whether such a row is *actually* deliverable is a
    separate, I/O-bearing question answered by a :data:`LiveParentResolver`.
    """
    target = str(record.get("target_session_id") or "").strip()
    return target.startswith(SYNTHETIC_FIXTURE_TARGET_PREFIX)


def _aware(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp into an aware UTC datetime, or None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _backoff_seconds(attempts: int, *, base: float, cap: float) -> float:
    """Exponential backoff for the ``attempts``-th failure (0-indexed)."""
    if attempts <= 0:
        return base
    return min(cap, base * (2.0**attempts))


def classify_wakes(
    records: list[Mapping[str, Any]],
    *,
    now: datetime,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    resolve_live_parent: LiveParentResolver | None = None,
) -> dict[str, list[Mapping[str, Any]]]:
    """Bucket discovered wake rows without any side effects.

    ``eligible`` rows are ready for a ``claim_wake`` attempt now; the rest are
    reported so a caller (dry-run, sweep, monitoring) can see exactly why each
    row was held back.

    ``synthetic_fixture`` isolates ``cli-*`` smoke-test placeholder bindings so
    they are *classified, never hidden*: a fixture row stays counted and visible
    but is kept out of ``eligible`` (and therefore undeliverable) unless the
    injected ``resolve_live_parent`` confirms its parent is a live native
    binding.  With no resolver (the pure default) the classification fails
    closed — a fixture is never treated as deliverable — which is exactly the
    read-only dry-run contract.
    """
    buckets: dict[str, list[Mapping[str, Any]]] = {
        "eligible": [],
        "skipped_young": [],
        "skipped_backoff": [],
        "synthetic_fixture": [],
        "exhausted": [],
    }
    for record in records:
        if is_synthetic_fixture_binding(record):
            live_parent = bool(resolve_live_parent(record)) if resolve_live_parent is not None else False
            if not live_parent:
                # A cli-* placeholder with no live native parent: keep it visible
                # for alerting but never deliverable.
                buckets["synthetic_fixture"].append(record)
                continue
            # A real live native parent happens to carry a cli-* id: fall through
            # to the normal age/backoff/attempt gates so it can still be delivered.
        attempts = int(record.get("wake_attempts") or 0)
        if attempts >= max_attempts:
            buckets["exhausted"].append(record)
            continue
        created = _aware(record.get("created_at"))
        if created is not None and (now - created).total_seconds() < min_age_seconds:
            buckets["skipped_young"].append(record)
            continue
        next_retry = _aware(record.get("wake_next_retry_at"))
        if next_retry is not None and next_retry > now:
            buckets["skipped_backoff"].append(record)
            continue
        buckets["eligible"].append(record)
    return buckets


def sweep_terminal_wakes(
    store: QuietCoordinationStore,
    deliver: Deliver,
    *,
    now: datetime | None = None,
    target_session_id: str = "",
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    limit: int = DEFAULT_LIMIT,
    resolve_live_parent: LiveParentResolver | None = None,
) -> dict[str, Any]:
    """Re-drive undelivered terminal wakes through the existing claim CAS.

    Returns a structured report (all-lists-of-message-ids) suitable for JSON,
    logging, or assertions.  Performs no time or transport I/O except through
    the injected ``now``, ``deliver``, and ``resolve_live_parent``.  ``cli-*``
    smoke-test fixture rows are surfaced in their own ``synthetic_fixture``
    bucket and never handed to ``deliver`` unless ``resolve_live_parent``
    confirms a live native parent.
    """
    moment = now or datetime.now(timezone.utc)
    discovered = store.pending_terminal_wakes(target_session_id=target_session_id, limit=limit)
    buckets = classify_wakes(
        discovered,
        now=moment,
        min_age_seconds=min_age_seconds,
        max_attempts=max_attempts,
        resolve_live_parent=resolve_live_parent,
    )
    report: dict[str, Any] = {
        "scanned": len(discovered),
        "delivered": [],
        "failed": [],
        "skipped_young": [str(r.get("message_id") or "") for r in buckets["skipped_young"]],
        "skipped_backoff": [str(r.get("message_id") or "") for r in buckets["skipped_backoff"]],
        "skipped_claimed": [],
        "synthetic_fixture": [str(r.get("message_id") or "") for r in buckets["synthetic_fixture"]],
        "exhausted": [str(r.get("message_id") or "") for r in buckets["exhausted"]],
        "observed_at": moment.isoformat(),
    }
    for record in buckets["eligible"]:
        message_id = str(record.get("message_id") or "")
        if not message_id:
            continue
        if not store.claim_wake(message_id):
            # A concurrent hot-path delivery already owns this token.
            report["skipped_claimed"].append(message_id)
            continue
        try:
            delivered = bool(deliver(record))
        except Exception:  # noqa: BLE001 - a raising sender is a failed delivery
            delivered = False
        if delivered:
            report["delivered"].append(message_id)
            continue
        attempts = int(record.get("wake_attempts") or 0)
        delay = _backoff_seconds(attempts, base=backoff_base_seconds, cap=max_backoff_seconds)
        next_retry_at = (moment + timedelta(seconds=max(0.0, delay))).isoformat()
        store.defer_wake_retry(message_id, next_retry_at=next_retry_at)
        report["failed"].append(message_id)
    return report


def control_plane_deliver(record: Mapping[str, Any]) -> bool:
    """Scheduler-facing sender: deliver one wake via the agent control plane.

    This is the real transport a cron/heartbeat injects into
    :func:`sweep_terminal_wakes`.  It mirrors the hot-path message shape used by
    ``managed_agent_completion`` and ``pty_routes`` and reports ``ok is False``
    (or any exception) as a failed delivery.  Kept import-lazy so the pure sweep
    module never pulls in the daemon control plane.
    """
    parent = str(record.get("target_session_id") or "").strip()
    message_id = str(record.get("message_id") or "").strip()
    if not parent or not message_id:
        return False
    child = str(record.get("source_session_id") or "").strip()
    outcome = str((record.get("payload") or {}).get("outcome") or "terminal")
    from omnicompany.packages.services._core.agent.control_plane import send_agent_input

    delivery = send_agent_input(
        parent,
        (
            "[OMNI CHILD TERMINAL WAKE · sweep] 子会话终态回执此前未送达，已由巡检重投。"
            f" child={child} outcome={outcome} message_id={message_id}。"
            " 请立即读取该回执并继续当前计划。"
        ),
        submit=True,
    )
    return not (isinstance(delivery, dict) and delivery.get("ok") is False)


def native_live_parent_binding(record: Mapping[str, Any]) -> bool:
    """Scheduler-facing gate: does this wake's parent resolve to a live worker?

    A ``cli-*`` fixture row is only ever deliverable if its ``target_session_id``
    actually names a live native worker the daemon currently hosts.  This asks
    the daemon's live session listing and fails closed (returns ``False``) on any
    error — a down or unreachable daemon can never *promote* a fixture to real
    delivery, matching the "insufficient proof ⇒ do not send" discipline.  Kept
    import-lazy so the pure sweep module never pulls in the control plane.
    """
    parent = str(record.get("target_session_id") or "").strip()
    if not parent:
        return False
    try:
        from omnicompany.core.parent_action_delivery import _native
        from omnicompany.packages.services._core.agent.control_plane import list_agent_workers

        live: set[str] = set()
        for row in list_agent_workers():
            for key in ("id", "session_id", "provider_session_id", "claude_session_id"):
                value = str(row.get(key) or "").strip()
                if value:
                    live.add(value)
                    live.add(_native(value))
        return parent in live or _native(parent) in live
    except Exception:  # noqa: BLE001 - an unreachable daemon is "no live parent"
        return False


def run_terminal_wake_sweep(
    store: QuietCoordinationStore | None = None,
    *,
    deliver: Deliver | None = None,
    now: datetime | None = None,
    resolve_live_parent: LiveParentResolver | None = None,
    **policy: Any,
) -> dict[str, Any]:
    """Bind the real control-plane sender and run one sweep.

    This is the single entry a scheduler (cron/heartbeat) calls.  It is not wired
    to any cron/hook in this change: delivery stays inert until an explicit caller
    invokes it, and tests drive :func:`sweep_terminal_wakes` with a fake sender.
    Real delivery binds :func:`native_live_parent_binding` so a ``cli-*`` fixture
    stays ineligible unless its parent is a live native worker.
    """
    return sweep_terminal_wakes(
        store or QuietCoordinationStore(),
        deliver or control_plane_deliver,
        now=now,
        resolve_live_parent=resolve_live_parent or native_live_parent_binding,
        **policy,
    )


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF_SECONDS",
    "DEFAULT_MIN_AGE_SECONDS",
    "SYNTHETIC_FIXTURE_TARGET_PREFIX",
    "classify_wakes",
    "control_plane_deliver",
    "is_synthetic_fixture_binding",
    "native_live_parent_binding",
    "run_terminal_wake_sweep",
    "sweep_terminal_wakes",
]
