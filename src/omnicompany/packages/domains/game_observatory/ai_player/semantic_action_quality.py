"""Canonical quality samples for coordinate-free semantic facility actions."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .action_quality_producer import stable_action_quality_sample_id
from .contracts import ActionQualitySampleV1, EvidenceReferenceV1


def produce_semantic_action_quality_sample(
    *,
    environment_id: str,
    session_id: str,
    task_id: str,
    command_id: str,
    action_run_id: str,
    evidence_step_id: str,
    effect_proof: Mapping[str, Any],
    evidence_refs: list[EvidenceReferenceV1],
    created_at: str,
) -> ActionQualitySampleV1:
    """Derive only facts proven by one terminal semantic-effect bundle."""

    expected = effect_proof.get("expected")
    actual = effect_proof.get("actual")
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        raise ValueError("semantic quality requires structured expected/actual effect proof")
    predicate = str(expected.get("predicate") or "").strip()
    if not predicate or actual.get("satisfied") is not True:
        raise ValueError("semantic quality effect predicate is absent or unsatisfied")
    expected_change = json.dumps(
        {"expected": dict(expected), "actual": dict(actual)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if predicate == "semantic-surface-changed":
        before = str(actual.get("before_semantic_sha256") or "")
        after = str(actual.get("after_semantic_sha256") or "")
        matched = bool(before and after and before != after)
    elif predicate == "any-semantic-marker":
        required = set(expected.get("required_markers") or ())
        observed = set(actual.get("observed_markers") or ())
        matched = bool(required and required.intersection(observed))
    elif predicate == "active-run-surface":
        matched = actual.get("active_run_surface") is True
    elif predicate == "route-map-visible":
        matched = actual.get("route_map_visible") is True
    else:
        raise ValueError("semantic quality effect predicate is unsupported")

    return ActionQualitySampleV1(
        id=stable_action_quality_sample_id(command_id),
        environment_id=environment_id,
        session_id=session_id,
        task_id=task_id,
        command_id=command_id,
        action_run_id=action_run_id,
        evidence_step_id=evidence_step_id,
        decision_mode="known_state",
        execution_disposition="executed",
        preflight_disposition="passed",
        outcome="confirmed" if matched else "failed",
        expected_change=expected_change,
        expected_change_measurement_status="measured",
        expected_change_matched=matched,
        adapter_call_count=1,
        evidence_complete=matched,
        meaningful_change=matched,
        task_progress=matched,
        token_measurement_status="unavailable",
        evidence_refs=evidence_refs,
        created_at=created_at,
    )
