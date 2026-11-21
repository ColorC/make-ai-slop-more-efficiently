"""Runtime half of the one-shot Codex capacity migration.

Everything here is a side effect on an existing facility: a sealed handoff
record (`omni handoff`), the write-claim ledger (`omni hr claim`), the session
binding ledger, and the canonical Agent Worker spawn route.  No parallel state
store is introduced — the retry state file remains the only capacity state.

Order is deliberate and fail-closed:

1. collect inheritance from durable sources (binding, native goal, plan
   progress, claims, retry chain);
2. write and verify the handoff document, then seal it;
3. re-check that the old session is really idle;
4. release the old session's write claims;
5. spawn the successor and attach it to the handoff record.

A failure before step 5 leaves no successor; the chain is released for at most
one further attempt by the caller.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from omnicompany.core import handoffs
from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.codex_capacity_retry import default_store_path
from omnicompany.packages.services._core.codex_capacity_migration import (
    MigrationContext,
    build_sections,
    render_document,
    successor_seed,
)


MIGRATION_REASON = "codex-capacity-migration"


def _claims_dir() -> Path:
    return omni_workspace_root() / "data" / "hr"


def _existing_rel(paths: list[str]) -> list[str]:
    root = omni_workspace_root()
    out: list[str] = []
    for value in paths:
        text = str(value or "").strip()
        if not text:
            continue
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = root / text
        try:
            if candidate.exists():
                out.append(text)
        except OSError:
            continue
    return out


def _plan_entries(plan: str, limit: int = 8) -> list[str]:
    if not plan:
        return []
    try:
        from omnicompany.packages.services.boss_sight.progress import list_entries

        rows = list_entries("plan", plan) or []
    except Exception:  # noqa: BLE001 - inheritance must never fail on a ledger read
        return []
    return [f"进度 {str(row.get('created_at') or '')[:19]}: {row.get('text')}" for row in rows[-limit:]]


def _plan_progress_tail(plan: str, lines: int = 12) -> list[str]:
    if not plan:
        return []
    path = omni_workspace_root() / "docs" / "plans" / plan / "progress.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    return [f"progress.md: {row}" for row in rows[-lines:]]


def _active_claims(trace_id: str) -> list[Any]:
    if not trace_id:
        return []
    try:
        from omnicompany.packages.services._core.hr.claim import read_claims

        return [row for row in read_claims(data_dir=_claims_dir()) if row.owner_trace_id == trace_id]
    except Exception:  # noqa: BLE001
        return []


def _release_claims(claims: list[Any], trace_id: str, handoff_id: str) -> list[dict[str, Any]]:
    """Release the stuck session's claims so the successor can re-declare them."""
    from omnicompany.packages.services._core.hr.claim import ClaimRejected, release_claim

    released: list[dict[str, Any]] = []
    for claim in claims:
        row = claim.to_dict()
        try:
            result = release_claim(
                claim.id,
                data_dir=_claims_dir(),
                owner_trace_id=trace_id,
                reason=f"{MIGRATION_REASON}:{handoff_id}",
            )
            row["status"] = result.status
            row["release_reason"] = result.release_reason
        except (ClaimRejected, OSError) as exc:
            # Not ours to release: it stays active and is reported instead.
            row["status"] = f"kept:{type(exc).__name__}"
        released.append(row)
    return released


def _binding(record: Any) -> dict[str, Any]:
    from omnicompany.packages.services._core.identity import binding_for_session

    try:
        return dict(binding_for_session(
            pty_id=record.session_id,
            session_id=record.provider_session_id,
            provider=record.provider,
        ) or {})
    except Exception:  # noqa: BLE001
        return {}


def _native_goal_text(provider: str, provider_session_id: str, home: Path | None) -> dict[str, Any]:
    if home is None or not provider_session_id:
        return {"known": False, "status": "unknown", "objective": ""}
    try:
        from .codex_capacity_goal import read_native_goal_objective

        return dict(read_native_goal_objective(provider, provider_session_id, provider_home=home))
    except Exception:  # noqa: BLE001
        return {"known": False, "status": "unknown", "objective": ""}


def _session_runtime(session_id: str) -> tuple[Any, Path | None, str]:
    """Live PTY session, its verified provider HOME, and its cwd."""
    from .pty import get_manager
    from .codex_runtime_identity import resolve_codex_runtime_identity

    sess = get_manager().get(session_id)
    if sess is None:
        return None, None, ""
    identity = resolve_codex_runtime_identity(sess.child_pid)
    home = Path(identity["provider_home"]) if identity.get("known") else None
    return sess, home, str(getattr(sess, "cwd", "") or "")


def build_context(record: Any, snapshot: Any, *, handoff_id: str, trigger: str) -> tuple[MigrationContext, dict[str, Any]]:
    """Collect the inheritance payload from durable sources only."""
    from omnicompany.packages.services._core.codex_capacity_evidence import resolve_rollout

    binding = _binding(record)
    sess, home, cwd = _session_runtime(record.session_id)
    cwd = cwd or str(binding.get("cwd") or "")
    goal = _native_goal_text(record.provider, record.provider_session_id, home)
    plan = str(binding.get("active_plan") or "")
    rollout = resolve_rollout(home, record.provider_session_id) if home else None
    state_path = handoffs.to_rel(default_store_path())
    failure = dict(getattr(snapshot, "capacity_failure", None) or {})
    chain = {
        "first_seen_at": _stamp(record.first_seen_at),
        "attempts": record.attempts,
        "last_attempt_at": _stamp(record.last_attempt_at),
        "failed_at": _stamp(failure.get("failed_at")),
        "trigger_detail": _trigger_detail(trigger, record, failure),
    }
    claims = _active_claims(str(binding.get("trace_id") or ""))
    objective = str(goal.get("objective") or "").strip()
    remaining = [f"继续原 goal: {objective}"] if objective else []
    if plan:
        remaining.append(f"按计划 `{plan}` 的 progress.md 未决项继续, 完成后照常追加进度")
    entry_paths = _existing_rel([
        f"docs/plans/{plan}/plan.md" if plan else "",
        f"docs/plans/{plan}/progress.md" if plan else "",
        state_path,
        "AGENTS.md",
    ])
    verify_commands = [
        "omni codex-capacity-retry status",
        f"omni handoff show {handoff_id}",
    ]
    if cwd:
        verify_commands.append(f'git -C "{cwd}" status --porcelain')
    context = MigrationContext(
        handoff_id=handoff_id,
        session_id=record.session_id,
        provider=record.provider,
        provider_session_id=record.provider_session_id,
        runtime_host=record.runtime_host,
        cwd=cwd,
        trigger=trigger,
        goal=goal,
        binding={
            "project": binding.get("project"),
            "workstream": binding.get("workstream"),
            "plan": plan,
            "task": binding.get("task_id"),
            "topic": binding.get("topic"),
            "trace_id": binding.get("trace_id"),
        },
        chain=chain,
        plan_progress=_plan_entries(plan) + _plan_progress_tail(plan),
        completed_evidence=[],
        remaining_work=remaining,
        claims=[claim.to_dict() for claim in claims],
        verify_commands=verify_commands,
        entry_paths=entry_paths,
        transcript=str(rollout) if rollout else "",
        state_path=state_path,
    )
    facts = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "session": {
            "provider": record.provider,
            "trace_id": binding.get("trace_id"),
            "session_id": record.provider_session_id,
            "pty_id": record.session_id,
            "started_at": getattr(sess, "started_at", None),
            "cwd": cwd,
        },
        "binding": {
            "plan": plan,
            "project": binding.get("project"),
            "workstream": binding.get("workstream"),
            "task": binding.get("task_id"),
            "topic": binding.get("topic"),
        },
        "transcript": str(rollout) if rollout else None,
        "git": {},
        "progress": [],
    }
    return context, {"facts": facts, "binding": binding, "claims": claims, "cwd": cwd, "plan": plan}


def _stamp(value: Any) -> str:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    if seconds <= 0:
        return "unknown"
    return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")


def _trigger_detail(trigger: str, record: Any, failure: Mapping[str, Any]) -> str:
    if trigger == "repeated-capacity":
        return (
            f"连续两次原地重试({_stamp(record.prev_attempt_at)} → {_stamp(record.last_attempt_at)},"
            f" 间隔 {int(float(record.last_attempt_at or 0) - float(record.prev_attempt_at or 0))} 秒)"
            f"之后仍有真实 provider 容量错误({_stamp(failure.get('failed_at'))})"
        )
    return (
        f"一次恢复({_stamp(record.recovered_at)})之后 30 分钟内没有任何真实 provider 进展"
    )


def _seal(doc_record: dict[str, Any]) -> dict[str, str]:
    """Same mirror semantics as ``omni handoff seal``, in process."""
    mirrored: dict[str, str] = {}
    binding = doc_record.get("binding") or {}
    workstream = binding.get("workstream")
    if workstream:
        try:
            from omnicompany.core.workstreams import add_checkpoint

            add_checkpoint(
                workstream,
                f"[handoff] {doc_record['title']}",
                kind="handoff",
                refs=[doc_record["doc"], f"handoff:{doc_record['id']}"],
                source=doc_record.get("from") or {},
            )
            mirrored["workstream"] = workstream
        except Exception as exc:  # noqa: BLE001
            mirrored["workstream_error"] = str(exc)
    plan = binding.get("plan")
    if plan:
        try:
            from omnicompany.packages.services.boss_sight.progress import add_entry

            add_entry("plan", plan,
                      f"容量迁移交接 {doc_record['id']}: {doc_record['title']} → {doc_record['doc']}",
                      by="codex-capacity-migration")
            mirrored["plan"] = plan
        except Exception as exc:  # noqa: BLE001
            mirrored["plan_error"] = str(exc)
    doc_record["status"] = "ready"
    handoffs.save_record(doc_record)
    return mirrored


async def _spawn(seed: str, *, provider: str, cwd: str, binding: Mapping[str, Any],
                 handoff_id: str, parent_pty: str) -> dict[str, Any]:
    from .pty_routes import CreateAgentWorkerBody, create_agent_worker

    body = CreateAgentWorkerBody(
        provider=provider,
        task=seed,
        cwd=cwd or None,
        project=binding.get("project") or None,
        workstream=binding.get("workstream") or None,
        active_plan=binding.get("active_plan") or None,
        topic=f"容量迁移接手 {handoff_id}"[:200],
        parent_agent_id=parent_pty,
        origin="agent",
        visibility="visible",
    )
    return await create_agent_worker(body, None)


async def perform_migration(
    record: Any,
    snapshot: Any,
    *,
    resnapshot: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Seal the inheritance, hand over the claims, and spawn the successor."""

    now = datetime.now(timezone.utc)
    trigger = str(getattr(record, "migration_trigger", "") or "migration")
    title = f"Codex 容量迁移接续 {record.session_id[:12]}"
    handoff_id = handoffs.new_id("handoff", title, now)
    context, extra = build_context(record, snapshot, handoff_id=handoff_id, trigger=trigger)
    binding, facts = extra["binding"], extra["facts"]

    doc_record, _ = handoffs.create_record(
        kind="handoff",
        title=title,
        reason=MIGRATION_REASON,
        facts=facts,
        plan=extra["plan"] or None,
        project=binding.get("project"),
        workstream=binding.get("workstream"),
        task=binding.get("task_id"),
        now=now,
    )
    doc_record["id"] = handoff_id
    doc_path = handoffs.doc_target("handoff", plan=extra["plan"] or None,
                                  slug=handoffs.slugify(f"capacity {record.session_id}", 24), now=now)
    doc_record["doc"] = handoffs.to_rel(doc_path)
    text = render_document(handoffs.render_draft(doc_record, facts), build_sections(context))
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(text, encoding="utf-8")
    handoffs.save_record(doc_record)

    problems = handoffs.verify_doc(doc_path, "handoff", record=doc_record)
    if problems:
        return {"ok": False, "reason": f"handoff-verify-failed:{problems[0]}", "handoff_id": handoff_id}
    _seal(doc_record)

    # The old session must be provably idle before anything is handed over.
    if resnapshot is not None:
        current = await resnapshot() if asyncio.iscoroutinefunction(resnapshot) else resnapshot()
        if current is None or not current.alive or current.working or current.pending_input:
            return {"ok": False, "reason": "old-session-active-again", "handoff_id": handoff_id}
        if current.generation != record.generation or current.provider_session_id != record.provider_session_id:
            return {"ok": False, "reason": "stale-session-generation", "handoff_id": handoff_id}

    released = _release_claims(extra["claims"], str(binding.get("trace_id") or ""), handoff_id)
    if released:
        doc_record.setdefault("notes", []).append({
            "at": now.isoformat(), "by": MIGRATION_REASON,
            "text": "释放原会话写入 claim: " + ", ".join(str(row.get("id")) for row in released),
        })
        handoffs.save_record(doc_record)

    seed = successor_seed(
        handoffs.seed_prompt(doc_record, doc_abs=str(doc_path)),
        context,
    )
    try:
        meta = await _spawn(
            seed,
            provider=record.provider,
            cwd=extra["cwd"],
            binding={**binding, "active_plan": extra["plan"]},
            handoff_id=handoff_id,
            parent_pty=record.session_id,
        )
    except Exception as exc:  # noqa: BLE001 - the chain stays reserved for one retry
        return {"ok": False, "reason": f"spawn-failed:{type(exc).__name__}:{exc}"[:300],
                "handoff_id": handoff_id}
    successor = str(meta.get("pty_id") or meta.get("id") or "")
    if not successor:
        return {"ok": False, "reason": "spawn-returned-no-session", "handoff_id": handoff_id}

    if binding.get("task_id"):
        try:
            from omnicompany.packages.services._core.identity import update_session_binding

            update_session_binding(successor, task_id=binding.get("task_id"))
        except Exception:  # noqa: BLE001
            pass
    handoffs.attach_successor(doc_record, {
        "provider": record.provider,
        "pty_id": successor,
        "worker_id": successor,
        "mode": "capacity-migration",
        "from_session_id": record.session_id,
        "trigger": trigger,
    })
    return {
        "ok": True,
        "handoff_id": handoff_id,
        "successor_session_id": successor,
        "doc": doc_record["doc"],
        "released_claims": [str(row.get("id")) for row in released],
        "reason": f"migrated:{trigger}",
        "at": time.time(),
    }


__all__ = ["MIGRATION_REASON", "build_context", "perform_migration"]
