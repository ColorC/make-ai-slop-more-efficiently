"""Shared proof checks for immediately replayable direct live interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import TransitionEdgeV1

if TYPE_CHECKING:
    from .store import AIPlayerStore


def canonical_direct_live_step_confirms_edge(
    store: AIPlayerStore,
    edge: TransitionEdgeV1,
) -> bool:
    """Accept a passed guarded CLI step as the first deterministic demonstration."""

    step_ids = {
        step_id
        for reference in edge.evidence_refs
        for step_id in reference.evidence_step_ids
    }
    if not step_ids:
        return False
    for step_id in step_ids:
        step = store.observatory_store.get_evidence_step(step_id)
        if step is None:
            return False
        run = store.observatory_store.get_evidence_run(step.evidence_run_id)
        live = step.live_evaluation
        terminal = step.terminal_evaluation
        if not all(
            (
                run is not None,
                run is not None and run.status == "passed" and bool(run.ended_at),
                run is not None
                and run.environment.get("caller") == "ai-player-live-step",
                run is not None and run.environment.get("actor") == "ai-player-cli",
                run is not None
                and not run.environment.get("skill_replay_version_id"),
                step.status == "passed",
                bool(step.ended_at),
                bool(step.before_frame_id),
                bool(step.after_frame_id),
                not step.quality_issues,
                step.action == edge.action,
                step.target_bounds == edge.target_bounds,
                live is not None and live.expectation_met and not live.stop_recommended,
                terminal is not None and terminal.passed,
            )
        ):
            return False
    return True


__all__ = ["canonical_direct_live_step_confirms_edge"]