# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.service.py"
"""Lifecycle queue facade and bounded companion worker service."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping

from omnicompany.core.config import omni_workspace_root

from .actions import validate_actions
from .agent import AgentProposal, CompanionAgent
from .executor import ActionExecutionError, ActionExecutor
from .models import CompanionEvent, build_event
from .policy import (
    feedback_context_requested,
    material_submission_allowed,
    referenced_material_ids,
)
from .store import CompanionStore
from .todo_lifecycle import maintain_event_from_queue


class CompanionService:
    """Coordinate queue claims, Agent proposals and deterministic writes."""

    def __init__(
        self,
        store: CompanionStore | None = None,
        *,
        proposer: Callable[[CompanionEvent, dict[str, Any]], AgentProposal | dict[str, Any]] | None = None,
        executor: ActionExecutor | None = None,
    ) -> None:
        self.store = store or CompanionStore()
        self._agent = CompanionAgent(self.store)
        self._proposer = proposer or self._agent.propose
        self.executor = executor or ActionExecutor(coordination_store=self.store)

    def context_for(self, event: CompanionEvent) -> dict[str, Any]:
        from omnicompany.packages.services._core.identity import (
            binding_for_session,
            records_for_session,
        )

        binding = binding_for_session(
            trace_id=event.trace_id,
            provider=event.provider,
            session_id=event.session_id,
        )
        materials: list[dict[str, Any]] = []
        feedback_needed = feedback_context_requested(event.user_input)
        if feedback_needed:
            try:
                store = self.executor.review_store
                direct_ids = referenced_material_ids(event.user_input)
                if direct_ids:
                    candidates = [item for material_id in direct_ids if (item := store.get(material_id)) is not None]
                else:
                    linked_ids = {
                        str(row.get("id") or "")
                        for row in records_for_session(
                            str(binding.get("trace_id") or event.trace_id)
                        )
                        if str(row.get("kind") or "") == "review_material"
                    }
                    candidates = [
                        item
                        for item in store.list(include_archived=False, include_internal=False)
                        if item.id in linked_ids
                        or (
                            bool(event.project)
                            and str(getattr(item, "project", "") or "") == event.project
                        )
                    ]
                for item in candidates[:8]:
                    materials.append({
                        "id": item.id,
                        "title": item.title,
                        "status": _enum(item.status),
                        "tier": _enum(item.tier),
                        "project": getattr(item, "project", ""),
                        "plan": item.source_plan_id or "",
                        "created_at": item.created_at,
                    })
            except Exception:  # noqa: BLE001 - context enrichment is fail-open
                materials = []
        return {
            "binding": binding,
            "maintenance_sinks": {
                "conversation_history": "agent_digest",
                "durable_user_decisions": "decisions_library",
                "review_deliverables": "reviewstage",
            },
            "review_context": {
                "creation_mode": "explicit_current_turn_only",
                "creation_allowed": material_submission_allowed(event.user_input),
                "max_new_materials": 1,
                "feedback_context_requested": feedback_needed,
                "existing_materials": materials,
            },
        }

    def process_claimed(self, event: CompanionEvent) -> dict[str, Any]:
        proposal_audit: dict[str, Any] = {}
        actions: dict[str, Any] = {}
        try:
            todo_receipt = maintain_event_from_queue(event)
        except Exception as exc:  # noqa: BLE001 - TODO maintenance must not break digest
            todo_receipt = {
                "state": "unknown",
                "reason": f"{type(exc).__name__}: {exc}",
                "needs_classification": True,
            }
        try:
            proposed = self._proposer(event, self.context_for(event))
            if isinstance(proposed, AgentProposal):
                actions = proposed.actions
                proposal_audit = proposed.audit
            elif isinstance(proposed, dict):
                actions = proposed
                proposal_audit = {"provider": "injected"}
            else:
                raise TypeError("companion proposer must return AgentProposal or dict")
            # AgentProposal is normalized by CompanionAgent, while injected
            # proposers are useful for tests and alternate runtimes. Keep the
            # service boundary authoritative by validating both paths again.
            actions = validate_actions(actions, event)
            receipts = [{"kind": "session_todo_lifecycle", **todo_receipt}]
            receipts.extend(self.executor.execute(event, actions))
            path = self.store.complete(
                event.event_id,
                receipts=receipts,
                agent=proposal_audit,
                proposal=actions,
            )
            audit = self.store.get(event.event_id)["audit"]
            return {
                "event_id": event.event_id,
                "state": "done",
                "audit_status": audit["status"],
                "receipts": receipts,
                "path": str(path),
            }
        except ActionExecutionError as exc:
            path = self.store.fail(
                event.event_id,
                f"{type(exc).__name__}: {exc}",
                agent=proposal_audit,
                receipts=exc.receipts,
                proposal=actions,
            )
            audit = self.store.get(event.event_id)["audit"]
            return {
                "event_id": event.event_id,
                "state": "failed",
                "audit_status": audit["status"],
                "finding_count": audit["finding_count"],
                "error": str(exc),
                "path": str(path),
            }
        except Exception as exc:
            path = self.store.fail(
                event.event_id,
                f"{type(exc).__name__}: {exc}",
                agent=proposal_audit,
                receipts=[],
                proposal=actions,
            )
            audit = self.store.get(event.event_id)["audit"]
            return {
                "event_id": event.event_id,
                "state": "failed",
                "audit_status": audit["status"],
                "finding_count": audit["finding_count"],
                "error": str(exc),
                "path": str(path),
            }

    def run_once(self) -> dict[str, Any] | None:
        claimed = self.store.claim_next()
        if claimed is None:
            return None
        event, _ = claimed
        return self.process_claimed(event)

    def run_pending(self, *, max_events: int = 8, concurrency: int = 2) -> list[dict[str, Any]]:
        claimed: list[CompanionEvent] = []
        for _ in range(max(1, max_events)):
            item = self.store.claim_next()
            if item is None:
                break
            claimed.append(item[0])
        if not claimed:
            return []
        lanes: dict[str, list[CompanionEvent]] = {}
        for event in claimed:
            lanes.setdefault(event.lane_key, []).append(event)
        workers = min(max(1, concurrency), 2, len(lanes))
        if workers == 1:
            return [self.process_claimed(event) for events in lanes.values() for event in events]
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="session-companion") as pool:
            futures = {
                pool.submit(self._process_lane, events): lane_key
                for lane_key, events in lanes.items()
            }
            for future in as_completed(futures):
                results.extend(future.result())
        return results

    def _process_lane(self, events: list[CompanionEvent]) -> list[dict[str, Any]]:
        """Preserve event order inside one project lane while lanes run in parallel."""

        return [self.process_claimed(event) for event in events]


def enqueue_lifecycle_event(
    *,
    trigger: str,
    trace_id: str,
    provider: str,
    session_id: str,
    cwd: str,
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
    store: CompanionStore | None = None,
    dry_run: bool = False,
    kick: bool = True,
) -> dict[str, Any]:
    """Public non-blocking enqueue entry used by hooks and the CLI."""

    store = store or CompanionStore()
    event = build_event(
        trace_id=trace_id,
        provider=provider,
        session_id=session_id,
        cwd=cwd,
        trigger=trigger,
        first_user_input=first_user_input,
        user_input=user_input,
        assistant_output=assistant_output,
        compact_summary=compact_summary,
        project=project,
        workstream=workstream,
        active_plan=active_plan,
        close_reason=close_reason,
        close_actor=close_actor,
        close_evidence=close_evidence,
        transcript_mtime=transcript_mtime,
        generation=generation,
    )
    result = store.enqueue(event, dry_run=dry_run)
    if result.get("created") and kick and not dry_run:
        result["worker_started"] = kick_background(store.root)
    return result


def kick_background(store_root: Path | str | None = None) -> bool:
    """Start one hidden, detached drain helper; a supervisor lock collapses races."""

    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("OMNI_TEST_ALLOW_SESSION_COMPANION"):
        return False
    if str(os.environ.get("OMNI_SESSION_COMPANION_AUTORUN") or "1").casefold() in {"0", "false", "off", "no"}:
        return False
    env = dict(os.environ)
    if store_root is not None:
        env["OMNI_SESSION_COMPANION_STATE_DIR"] = str(Path(store_root).resolve())
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(getattr(subprocess, "DETACHED_PROCESS", 0))
    try:
        subprocess.Popen(
            [sys.executable, "-m", "omnicompany.packages.services._core.session_companion.worker", "--drain"],
            cwd=str(omni_workspace_root()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError:
        return False
    return True


def _enum(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value or "")


__all__ = ["CompanionService", "enqueue_lifecycle_event", "kick_background"]
