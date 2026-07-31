"""Crash-safe second phase for deterministic skill-route evidence.

The immutable EvidenceRun/EvidenceStep/manifest bundle and signed SkillRun are
the durable queue.  A route or provider-turn boundary consumes that queue only
after the physical action and its terminal visual guard have finished.  No
second scheduler or mutable in-memory hand-off is introduced.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .contracts import SkillRunV1
from .live_evidence_state_ingest import (
    LiveEvidenceStateAutoIngestResultV1,
    auto_ingest_terminal_evidence_runs,
)
from .skill_attestation import skill_runtime_signer_and_trust_store
from .skills import SkillLifecycle
from .store import AIPlayerStore
from ..store import ObservatoryStore


class DeferredSkillSedimentationResultV1(BaseModel):
    """Visible, deterministic receipt for one queue drain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: str = Field(min_length=1)
    requested_skill_run_ids: list[str] = Field(default_factory=list)
    pending_skill_run_ids: list[str] = Field(default_factory=list)
    settled_skill_run_ids: list[str] = Field(default_factory=list)
    evidence_run_ids: list[str] = Field(default_factory=list)
    state_ingest: LiveEvidenceStateAutoIngestResultV1 | None = None


def _deferred_evidence_runs(
    observatory: ObservatoryStore,
    skill_run: SkillRunV1,
) -> list[tuple[str, tuple[str, ...]]]:
    """Return route-action runs explicitly enrolled in two-phase sedimentation."""

    selected: list[tuple[str, tuple[str, ...]]] = []
    referenced = {
        evidence_run_id
        for reference in skill_run.evidence_refs
        for evidence_run_id in reference.evidence_run_ids
    }
    for evidence_run_id in referenced:
        run = observatory.get_evidence_run(evidence_run_id)
        if run is None:
            raise ValueError(f"deferred SkillRun references missing EvidenceRun: {evidence_run_id}")
        deferred_marker = run.environment.get("defer_semantic_sedimentation")
        if deferred_marker is not None and not isinstance(deferred_marker, bool):
            raise ValueError(
                f"EvidenceRun has malformed deferred sedimentation marker: {evidence_run_id}"
            )
        if deferred_marker is not True:
            continue
        replay_version_id = run.environment.get("skill_replay_version_id")
        if not isinstance(replay_version_id, str) or not replay_version_id:
            raise ValueError(
                f"deferred EvidenceRun lacks a skill replay version: {evidence_run_id}"
            )
        if run.environment.get("environment_id") != skill_run.environment_id:
            raise ValueError(
                f"deferred EvidenceRun crosses skill environment: {evidence_run_id}"
            )
        if replay_version_id != skill_run.skill_version_id:
            # A SkillRun intentionally carries the source-step evidence from the
            # preceding route action so its provenance remains continuous.  That
            # source EvidenceRun may itself have used deferred sedimentation, but
            # it belongs to the preceding skill and is not work for this run's
            # queue item.  Only EvidenceRuns explicitly tagged with this exact
            # skill version are enrolled here.
            continue
        selected.append((run.id, tuple(run.step_ids)))
    return selected


def _terminal_run_needs_no_state_assignment(
    observatory: ObservatoryStore,
    evidence_run_id: str,
    step_ids: tuple[str, ...],
) -> bool:
    """Return whether terminal failure evidence is complete without a target state.

    A failed or stopped replay is still durable route evidence, but assigning its
    terminal frame to the skill's expected semantic state would turn a failure
    into a reusable false-success edge.  Such runs therefore finish the deferred
    queue after lifecycle reconciliation and never enter state ingestion.
    """

    run = observatory.get_evidence_run(evidence_run_id)
    if run is None or run.status not in {"failed", "stopped"} or not run.ended_at:
        return False
    if not step_ids:
        return False
    terminal_step_statuses = {"passed", "failed", "stopped"}
    return all(
        (step := observatory.get_evidence_step(step_id)) is not None
        and step.status in terminal_step_statuses
        and bool(step.ended_at)
        for step_id in step_ids
    )


def deferred_skill_run_is_settled(
    store: AIPlayerStore,
    skill_run: SkillRunV1,
) -> bool:
    """Fail closed until success has a state, or terminal failure needs none."""

    selected = _deferred_evidence_runs(store.observatory_store, skill_run)
    if not selected:
        return True
    return all(
        _terminal_run_needs_no_state_assignment(
            store.observatory_store,
            evidence_run_id,
            step_ids,
        )
        or store.find_current_state_assignment_for_evidence(
            skill_run.environment_id,
            evidence_step_ids=list(step_ids),
        )
        is not None
        for evidence_run_id, step_ids in selected
    )


def _guarded_deterministic_run_is_fast_path_eligible(
    observatory: ObservatoryStore,
    player: AIPlayerStore,
    *,
    evidence_run_id: str,
    step_ids: tuple[str, ...],
) -> bool:
    """Return whether the immutable action receipt carries both visual guards."""

    if len(step_ids) != 1:
        return False
    run = observatory.get_evidence_run(evidence_run_id)
    step = observatory.get_evidence_step(step_ids[0])
    if run is None or step is None:
        return False
    guard = run.environment.get("source_state_guard")
    if (
        not isinstance(guard, dict)
        or guard.get("binding_method") != "verified_skill_source_assignment"
        or not isinstance(guard.get("semantic_state_id"), str)
        or not isinstance(guard.get("observation_id"), str)
        or not isinstance(run.environment.get("expected_semantic_state_id"), str)
        or not isinstance(
            run.environment.get("expected_state_reference_artifact_id"),
            str,
        )
    ):
        return False
    assignment = player.get_current_state_assignment(
        run.scope_id,
        str(guard["observation_id"]),
    )
    if (
        assignment is None
        or assignment.status != "active"
        or assignment.state_id != guard["semantic_state_id"]
    ):
        return False
    stability = step.stability
    return bool(
        step.status == "passed"
        and step.ended_at
        and stability.settled
        and stability.trusted_reference_matched
        and stability.trusted_reference_artifact_id
        == run.environment.get("expected_state_reference_artifact_id")
        and stability.trusted_reference_distance is not None
        and stability.trusted_reference_max_distance is not None
        and stability.trusted_reference_distance
        <= stability.trusted_reference_max_distance
    )


def settle_deferred_skill_runs(
    store_root: Path,
    *,
    environment_id: str,
    skill_run_ids: Sequence[str] | None = None,
) -> DeferredSkillSedimentationResultV1:
    """Drain persisted route evidence at a route/turn boundary.

    Lifecycle reconciliation deliberately precedes state ingestion.  Therefore a
    lifecycle/provenance failure cannot leave a newly reusable route edge behind.
    Both operations are idempotent; a process may die between them and the next
    boundary will rediscover the same immutable SkillRun and retry safely.
    """

    observatory = ObservatoryStore(store_root.resolve())
    _signer, trust_store = skill_runtime_signer_and_trust_store(observatory.root)
    player = AIPlayerStore(
        observatory,
        skill_validator_trust_store=trust_store,
    )
    if player.get_environment(environment_id) is None:
        raise KeyError(f"unknown AI-player environment: {environment_id}")

    requested = list(dict.fromkeys(skill_run_ids or ()))
    if requested:
        runs: list[SkillRunV1] = []
        for run_id in requested:
            run = player.get_skill_run(environment_id, run_id)
            if run is None:
                raise ValueError(f"unknown SkillRun: {run_id}")
            runs.append(run)
    else:
        runs = player.list_pending_deferred_skill_runs(environment_id)

    pending: list[SkillRunV1] = []
    lifecycle_only: list[SkillRunV1] = []
    pending_evidence_run_ids: list[str] = []
    for run in runs:
        selected = _deferred_evidence_runs(observatory, run)
        if not selected:
            continue
        if deferred_skill_run_is_settled(player, run):
            if all(
                _terminal_run_needs_no_state_assignment(
                    observatory,
                    evidence_run_id,
                    step_ids,
                )
                for evidence_run_id, step_ids in selected
            ):
                lifecycle_only.append(run)
            continue
        pending.append(run)
        pending_evidence_run_ids.extend(
            evidence_run_id
            for evidence_run_id, step_ids in selected
            if not _terminal_run_needs_no_state_assignment(
                observatory,
                evidence_run_id,
                step_ids,
            )
        )

    lifecycle_runs = list({run.id: run for run in [*pending, *lifecycle_only]}.values())
    if not lifecycle_runs:
        return DeferredSkillSedimentationResultV1(
            environment_id=environment_id,
            requested_skill_run_ids=requested,
        )

    lifecycle = SkillLifecycle(player)
    for run in lifecycle_runs:
        # This verifies signed provenance at every lifecycle gate.  Any failure
        # aborts before the terminal state becomes eligible for route planning.
        lifecycle.reconcile_after_run(run)

    ordered_evidence_run_ids = list(dict.fromkeys(pending_evidence_run_ids))
    verified_skill_run_ids: dict[str, str] = {}
    for skill_run in pending:
        persisted_skill_run = player.get_skill_run(environment_id, skill_run.id)
        if persisted_skill_run is None:
            # Compatibility fixtures may provide an in-memory queue item.  Such
            # an item can use the original full ingest, but never the signed
            # deterministic shortcut.
            continue
        if persisted_skill_run != skill_run:
            raise ValueError(
                f"deferred SkillRun differs from its canonical record: {skill_run.id}"
            )
        player.verify_skill_run_provenance(persisted_skill_run)
        for evidence_run_id, step_ids in _deferred_evidence_runs(observatory, skill_run):
            if not _guarded_deterministic_run_is_fast_path_eligible(
                observatory,
                player,
                evidence_run_id=evidence_run_id,
                step_ids=step_ids,
            ):
                continue
            existing = verified_skill_run_ids.setdefault(evidence_run_id, skill_run.id)
            if existing != skill_run.id:
                raise ValueError(
                    "one deferred EvidenceRun is claimed by multiple signed SkillRuns: "
                    f"{evidence_run_id}"
                )
    def evidence_order(run_id: str) -> tuple[str, str]:
        evidence_run = observatory.get_evidence_run(run_id)
        if evidence_run is None:
            raise ValueError(f"deferred EvidenceRun disappeared: {run_id}")
        return str(evidence_run.ended_at or ""), run_id

    ordered_evidence_run_ids.sort(key=evidence_order)
    state_ingest = None
    if ordered_evidence_run_ids:
        state_ingest = auto_ingest_terminal_evidence_runs(
            observatory.root,
            environment_id=environment_id,
            evidence_run_ids=ordered_evidence_run_ids,
            verified_skill_run_ids=verified_skill_run_ids,
        )
        if state_ingest.result is None or not state_ingest.result.persistence_reopen_verified:
            raise ValueError("deferred route evidence lacks a reopen-verified state-ingest receipt")

    reopened = AIPlayerStore(
        ObservatoryStore(observatory.root),
        skill_validator_trust_store=trust_store,
    )
    unsettled = [
        run.id for run in lifecycle_runs if not deferred_skill_run_is_settled(reopened, run)
    ]
    if unsettled:
        raise ValueError(
            "deferred route evidence remains unsettled after reopen verification: "
            + ", ".join(unsettled)
        )
    return DeferredSkillSedimentationResultV1(
        environment_id=environment_id,
        requested_skill_run_ids=requested,
        pending_skill_run_ids=[run.id for run in lifecycle_runs],
        settled_skill_run_ids=[run.id for run in lifecycle_runs],
        evidence_run_ids=ordered_evidence_run_ids,
        state_ingest=state_ingest,
    )


__all__ = [
    "DeferredSkillSedimentationResultV1",
    "deferred_skill_run_is_settled",
    "settle_deferred_skill_runs",
]
