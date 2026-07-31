"""Fail-closed Gate 3 finite-domain saturation contract.

This module is deliberately internal QA state.  It is not imported by the
public compiler, API projection, or reverse-engineered design-spec models.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceRun, EvidenceStep, NormalizedAction, SourcePixelRect


Disposition = Literal[
    "verified_transition",
    "verified_state_change",
    "verified_no_change",
    "forbidden",
    "deferred",
]
SafetyClass = Literal["safe", "forbidden", "requires_authorization"]
AnswerabilityDimension = Literal["mechanics", "resources", "states"]
_BOUNDED_POINTER_ACTIONS = {"tap", "swipe", "pinch", "two_finger_swipe"}
ActionType = Literal[
    "tap",
    "swipe",
    "pinch",
    "two_finger_swipe",
    "text",
    "key",
    "launch",
    "wait",
    "back",
    "home",
    "mouse_move",
    "mouse_button",
    "gamepad_button",
    "gamepad_axis",
    "reset",
]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaturationSafetyBoundary(_StrictContract):
    allowed_safe_action_types: list[ActionType] = Field(min_length=1)
    forbidden_target_terms: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)


class FiniteDomainScope(_StrictContract):
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    build_scope_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    boundary: str = Field(min_length=1)
    entry_state_node_ids: list[str] = Field(min_length=1)
    safety_boundary: SaturationSafetyBoundary


class RecoveryPath(_StrictContract):
    kind: Literal["return", "rollback", "reset"]
    action: NormalizedAction
    target_bounds: SourcePixelRect | None = None
    returns_to_state_node_id: str = Field(min_length=1)
    evidence_step_ids: list[str] = Field(min_length=1)
    note: str = Field(default="", max_length=2000)


class VisibleInteractionCandidate(_StrictContract):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    bounds: SourcePixelRect
    action: NormalizedAction
    safety: SafetyClass
    disposition: Disposition | None = None
    disposition_reason: str = Field(default="", max_length=4000)
    outcome: str = Field(default="", max_length=4000)
    evidence_step_ids: list[str] = Field(default_factory=list)
    executed_step_ids: list[str] = Field(default_factory=list)
    child_state_node_ids: list[str] = Field(default_factory=list)
    transition_leaves_scope: bool = False
    recovery: RecoveryPath | None = None
    publishable_interaction: bool = False


class SaturationStateNode(_StrictContract):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    state_description: str = Field(min_length=1)
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    observation_step_ids: list[str] = Field(default_factory=list)
    candidate_enumeration_complete: bool = False
    candidates: list[VisibleInteractionCandidate] = Field(default_factory=list)
    review_note: str = Field(default="", max_length=4000)


class ReviewedStateEvidence(_StrictContract):
    state_node_id: str = Field(min_length=1)
    evidence_step_ids: list[str] = Field(min_length=1)


class CleanSaturationReview(_StrictContract):
    id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    evidence_run_id: str = Field(min_length=1)
    clean_start_ref: str = Field(min_length=1)
    started_from_clean: bool
    reviewed_state_node_ids: list[str] = Field(default_factory=list)
    state_evidence: list[ReviewedStateEvidence] = Field(default_factory=list)
    new_safe_candidate_ids: list[str] = Field(default_factory=list)
    evidence_step_ids: list[str] = Field(default_factory=list)
    passed: bool
    note: str = Field(default="", max_length=4000)


class HumanOmissionReview(_StrictContract):
    reviewer: str = Field(min_length=1)
    reviewed_state_node_ids: list[str] = Field(min_length=1)
    obvious_omission_candidate_ids: list[str] = Field(default_factory=list)
    passed: bool
    note: str = Field(min_length=1, max_length=4000)


class AnswerabilityCheck(_StrictContract):
    question: str = Field(min_length=1)
    fact_record_ref: str = Field(min_length=1)
    evidence_step_ids: list[str] = Field(min_length=1)


class DomainAnswerability(_StrictContract):
    dimension: AnswerabilityDimension
    answerable: bool
    checks: list[AnswerabilityCheck] = Field(default_factory=list)
    note: str = Field(default="", max_length=4000)


class FiniteDomainSaturationLedger(_StrictContract):
    """Internal exploration ledger; object ids are never a public projection."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    schema_id: Literal["game-observatory.finite-domain-saturation.v1"] = Field(
        default="game-observatory.finite-domain-saturation.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    scope: FiniteDomainScope
    state_nodes: list[SaturationStateNode] = Field(min_length=1)
    clean_reviews: list[CleanSaturationReview] = Field(default_factory=list)
    human_omission_review: HumanOmissionReview | None = None
    answerability: list[DomainAnswerability] = Field(default_factory=list)
    internal_note: str = Field(default="", max_length=8000)


class SaturationValidationIssue(_StrictContract):
    code: str
    path: str
    message: str


class SaturationValidationResult(_StrictContract):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    schema_id: Literal["game-observatory.finite-domain-saturation-validation.v1"] = Field(
        default="game-observatory.finite-domain-saturation-validation.v1",
        alias="schema",
    )
    ledger_id: str
    ok: bool
    saturation_pass: bool
    counts: dict[str, int]
    unresolved_candidate_ids: list[str]
    unreviewed_state_node_ids: list[str]
    issues: list[SaturationValidationIssue]


def load_saturation_ledger(path: Path) -> FiniteDomainSaturationLedger:
    return FiniteDomainSaturationLedger.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _fold(value: str) -> str:
    return "".join(value.casefold().split())


def _rect_within_viewport(
    rect: SourcePixelRect,
    *,
    width: int,
    height: int,
) -> bool:
    return rect.x + rect.width <= width and rect.y + rect.height <= height


def _action_geometry_issues(
    action: NormalizedAction,
    bounds: SourcePixelRect | None,
    *,
    width: int,
    height: int,
) -> list[str]:
    issues: list[str] = []
    if action.type in _BOUNDED_POINTER_ACTIONS and bounds is None:
        issues.append(f"{action.type} action has no target bounds")
    if action.type in _BOUNDED_POINTER_ACTIONS:
        if action.x is None or action.y is None:
            issues.append(f"{action.type} action has no source x/y")
        elif bounds is not None and not (
            bounds.x <= action.x < bounds.x + bounds.width
            and bounds.y <= action.y < bounds.y + bounds.height
        ):
            issues.append(f"{action.type} action starts outside candidate bounds")
    if action.type in {"swipe", "two_finger_swipe"}:
        if action.x2 is None or action.y2 is None:
            issues.append(f"{action.type} action has no source x2/y2")
        elif not (0 <= action.x2 < width and 0 <= action.y2 < height):
            issues.append(f"{action.type} action ends outside viewport")
    if action.type == "two_finger_swipe" and None not in (
        action.x,
        action.y,
        action.x2,
        action.y2,
    ):
        for label, x, y in (
            (
                "second-finger start",
                action.x + action.two_finger_offset_x,
                action.y + action.two_finger_offset_y,
            ),
            (
                "second-finger end",
                action.x2 + action.two_finger_offset_x,
                action.y2 + action.two_finger_offset_y,
            ),
        ):
            if not (0 <= x < width and 0 <= y < height):
                issues.append(f"two_finger_swipe {label} is outside viewport")
            elif bounds is not None and not (
                bounds.x <= x < bounds.x + bounds.width
                and bounds.y <= y < bounds.y + bounds.height
            ):
                issues.append(f"two_finger_swipe {label} is outside candidate bounds")
    for label, x, y in (
        ("start", action.x, action.y),
        ("end", action.x2, action.y2),
    ):
        if x is not None and y is not None and not (0 <= x < width and 0 <= y < height):
            issues.append(f"action {label} point is outside viewport")
    return issues


def _evidence_refs(
    ledger: FiniteDomainSaturationLedger,
) -> tuple[
    dict[str, set[str]],
    set[str],
    dict[str, list[tuple[str, str]]],
    dict[
        str,
        list[tuple[str, NormalizedAction, SourcePixelRect | None, int, int]],
    ],
]:
    paths: dict[str, set[str]] = defaultdict(set)
    publishable_steps: set[str] = set()
    expected_review_runs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    action_expectations: dict[
        str,
        list[tuple[str, NormalizedAction, SourcePixelRect | None, int, int]],
    ] = defaultdict(list)

    def add(step_ids: list[str], path: str) -> None:
        for step_id in step_ids:
            paths[step_id].add(path)

    for node in ledger.state_nodes:
        add(node.observation_step_ids, f"state_nodes.{node.id}.observation_step_ids")
        for candidate in node.candidates:
            candidate_path = f"state_nodes.{node.id}.candidates.{candidate.id}"
            add(candidate.evidence_step_ids, f"{candidate_path}.evidence_step_ids")
            add(candidate.executed_step_ids, f"{candidate_path}.executed_step_ids")
            for step_id in candidate.executed_step_ids:
                action_expectations[step_id].append(
                    (
                        f"{candidate_path}.executed_step_ids",
                        candidate.action,
                        candidate.bounds,
                        node.viewport_width,
                        node.viewport_height,
                    )
                )
            if candidate.publishable_interaction:
                publishable_steps.update(candidate.executed_step_ids)
            if candidate.recovery:
                add(
                    candidate.recovery.evidence_step_ids,
                    f"{candidate_path}.recovery.evidence_step_ids",
                )
                for step_id in candidate.recovery.evidence_step_ids:
                    action_expectations[step_id].append(
                        (
                            f"{candidate_path}.recovery.evidence_step_ids",
                            candidate.recovery.action,
                            candidate.recovery.target_bounds,
                            node.viewport_width,
                            node.viewport_height,
                        )
                    )
    for review in ledger.clean_reviews:
        add(review.evidence_step_ids, f"clean_reviews.{review.id}.evidence_step_ids")
        for state_evidence in review.state_evidence:
            evidence_path = (
                f"clean_reviews.{review.id}.state_evidence."
                f"{state_evidence.state_node_id}.evidence_step_ids"
            )
            add(state_evidence.evidence_step_ids, evidence_path)
            for step_id in state_evidence.evidence_step_ids:
                expected_review_runs[step_id].append(
                    (evidence_path, review.evidence_run_id)
                )
    for item in ledger.answerability:
        for index, check in enumerate(item.checks):
            add(
                check.evidence_step_ids,
                f"answerability.{item.dimension}.checks.{index}.evidence_step_ids",
            )
    return paths, publishable_steps, expected_review_runs, action_expectations


def validate_saturation_ledger(
    ledger: FiniteDomainSaturationLedger,
    *,
    store: Any | None = None,
) -> SaturationValidationResult:
    """Validate the complete declared domain and resolve evidence from the store.

    Passing no store is intentionally a failure whenever the ledger contains an
    evidence reference: strings alone are not proof that a recorder step exists.
    """

    issues: list[SaturationValidationIssue] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append(SaturationValidationIssue(code=code, path=path, message=message))

    node_ids = [node.id for node in ledger.state_nodes]
    node_id_set = set(node_ids)
    if len(node_ids) != len(node_id_set):
        add("duplicate_state_node_id", "state_nodes", "state node ids must be unique")

    for entry_id in ledger.scope.entry_state_node_ids:
        if entry_id not in node_id_set:
            add(
                "missing_entry_state",
                "scope.entry_state_node_ids",
                f"entry state does not resolve: {entry_id}",
            )

    candidate_ids: list[str] = []
    unresolved: set[str] = set()
    unreviewed: set[str] = set()
    child_edges: dict[str, set[str]] = defaultdict(set)
    allowed_actions = set(ledger.scope.safety_boundary.allowed_safe_action_types)
    forbidden_terms = [_fold(item) for item in ledger.scope.safety_boundary.forbidden_target_terms]

    for node in ledger.state_nodes:
        node_path = f"state_nodes.{node.id}"
        if not node.candidate_enumeration_complete:
            unreviewed.add(node.id)
            add(
                "state_review_incomplete",
                node_path,
                "visible candidate enumeration is not complete",
            )
        if not node.observation_step_ids:
            add(
                "state_missing_observation_evidence",
                f"{node_path}.observation_step_ids",
                "state node has no recorder step",
            )
        for candidate in node.candidates:
            candidate_ids.append(candidate.id)
            path = f"{node_path}.candidates.{candidate.id}"
            if not _rect_within_viewport(
                candidate.bounds,
                width=node.viewport_width,
                height=node.viewport_height,
            ):
                add(
                    "candidate_bounds_outside_viewport",
                    f"{path}.bounds",
                    "candidate bounds exceed the state viewport",
                )
            for geometry_issue in _action_geometry_issues(
                candidate.action,
                candidate.bounds,
                width=node.viewport_width,
                height=node.viewport_height,
            ):
                add("candidate_action_geometry_invalid", f"{path}.action", geometry_issue)

            if candidate.disposition is None:
                unresolved.add(candidate.id)
                add(
                    "candidate_unadjudicated",
                    f"{path}.disposition",
                    "visible candidate has no disposition",
                )
                continue

            if candidate.safety == "safe":
                if candidate.action.type not in allowed_actions:
                    add(
                        "safety_boundary_violation",
                        f"{path}.action.type",
                        "safe candidate action type is outside the declared boundary",
                    )
                if any(term and term in _fold(candidate.label) for term in forbidden_terms):
                    add(
                        "safety_boundary_violation",
                        f"{path}.label",
                        "safe candidate matches a forbidden target term",
                    )
                if candidate.disposition in {"forbidden", "deferred"}:
                    unresolved.add(candidate.id)
                    add(
                        "safe_candidate_not_verified",
                        f"{path}.disposition",
                        "safe candidate must have a verified disposition before saturation",
                    )
            elif candidate.safety == "forbidden" and candidate.disposition != "forbidden":
                add(
                    "safety_disposition_mismatch",
                    f"{path}.disposition",
                    "forbidden candidate must use the forbidden disposition",
                )
            elif (
                candidate.safety == "requires_authorization"
                and candidate.disposition != "deferred"
            ):
                add(
                    "safety_disposition_mismatch",
                    f"{path}.disposition",
                    "authorization-gated candidate must remain deferred",
                )

            if candidate.disposition in {"forbidden", "deferred"}:
                if not candidate.disposition_reason.strip():
                    add(
                        "disposition_reason_missing",
                        f"{path}.disposition_reason",
                        f"{candidate.disposition} disposition requires an explanation",
                    )
                if candidate.executed_step_ids:
                    add(
                        "safety_boundary_violation",
                        f"{path}.executed_step_ids",
                        "forbidden or deferred candidate records an execution",
                    )
            else:
                if not candidate.executed_step_ids:
                    add(
                        "verified_candidate_missing_execution_evidence",
                        f"{path}.executed_step_ids",
                        "verified candidate has no executed recorder step",
                    )
                if not candidate.outcome.strip():
                    add(
                        "verified_candidate_missing_outcome",
                        f"{path}.outcome",
                        "verified candidate has no factual outcome",
                    )

            if not set(candidate.executed_step_ids) <= set(candidate.evidence_step_ids):
                add(
                    "execution_evidence_not_declared",
                    f"{path}.executed_step_ids",
                    "executed steps must also be included in evidence_step_ids",
                )

            if candidate.disposition == "verified_transition":
                if candidate.child_state_node_ids and candidate.transition_leaves_scope:
                    add(
                        "ambiguous_transition_scope",
                        path,
                        "transition cannot both reference a child and leave the scope",
                    )
                if not candidate.child_state_node_ids and not candidate.transition_leaves_scope:
                    add(
                        "transition_target_missing",
                        f"{path}.child_state_node_ids",
                        "in-scope transition has no child state node",
                    )
                for child_id in candidate.child_state_node_ids:
                    child_edges[node.id].add(child_id)
                    if child_id not in node_id_set:
                        add(
                            "discovered_child_missing",
                            f"{path}.child_state_node_ids",
                            f"discovered child does not resolve: {child_id}",
                        )
                if candidate.child_state_node_ids and candidate.recovery is None:
                    add(
                        "transition_return_missing",
                        f"{path}.recovery",
                        "in-scope child transition has no evidenced return path",
                    )
            elif candidate.child_state_node_ids:
                add(
                    "unexpected_child_reference",
                    f"{path}.child_state_node_ids",
                    "only verified transitions may discover child states",
                )

            if candidate.disposition == "verified_state_change" and candidate.recovery is None:
                add(
                    "state_change_rollback_missing",
                    f"{path}.recovery",
                    "state change has no evidenced rollback or reset",
                )

            if candidate.recovery:
                recovery_path = f"{path}.recovery"
                if candidate.recovery.target_bounds is not None and not _rect_within_viewport(
                    candidate.recovery.target_bounds,
                    width=node.viewport_width,
                    height=node.viewport_height,
                ):
                    add(
                        "recovery_bounds_outside_viewport",
                        f"{recovery_path}.target_bounds",
                        "recovery bounds exceed the state viewport",
                    )
                if candidate.recovery.action.type not in allowed_actions:
                    add(
                        "safety_boundary_violation",
                        f"{recovery_path}.action.type",
                        "recovery action type is outside the declared safe boundary",
                    )
                if candidate.recovery.returns_to_state_node_id not in node_id_set:
                    add(
                        "recovery_target_missing",
                        f"{recovery_path}.returns_to_state_node_id",
                        "recovery target state does not resolve",
                    )
                elif candidate.recovery.returns_to_state_node_id != node.id:
                    add(
                        "recovery_does_not_close_state",
                        f"{recovery_path}.returns_to_state_node_id",
                        "recovery must return to the candidate source state",
                    )
                if not set(candidate.recovery.evidence_step_ids) <= set(
                    candidate.evidence_step_ids
                ):
                    add(
                        "recovery_evidence_not_declared",
                        f"{recovery_path}.evidence_step_ids",
                        "recovery steps must also be included in candidate evidence_step_ids",
                    )
                for geometry_issue in _action_geometry_issues(
                    candidate.recovery.action,
                    candidate.recovery.target_bounds,
                    width=node.viewport_width,
                    height=node.viewport_height,
                ):
                    add(
                        "recovery_action_geometry_invalid",
                        f"{recovery_path}.action",
                        geometry_issue,
                    )

            if candidate.publishable_interaction:
                if candidate.disposition not in {
                    "verified_transition",
                    "verified_state_change",
                    "verified_no_change",
                }:
                    add(
                        "publishable_interaction_unverified",
                        path,
                        "publishable interaction has no verified disposition",
                    )
                if not candidate.executed_step_ids:
                    add(
                        "publishable_interaction_missing_evidence",
                        f"{path}.executed_step_ids",
                        "publishable interaction has no complete action evidence",
                    )

    if len(candidate_ids) != len(set(candidate_ids)):
        add(
            "duplicate_candidate_id",
            "state_nodes.candidates",
            "candidate ids must be unique across the finite domain",
        )

    reachable: set[str] = set()
    queue = deque(
        item for item in ledger.scope.entry_state_node_ids if item in node_id_set
    )
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(sorted(child_edges.get(current, set()) - reachable))
    for node_id in sorted(node_id_set - reachable):
        add(
            "state_node_unreachable",
            f"state_nodes.{node_id}",
            "state node is not reachable from a declared entry",
        )

    complete_clean_reviews: list[CleanSaturationReview] = []
    all_nodes = set(node_ids)
    review_ids = [review.id for review in ledger.clean_reviews]
    if len(review_ids) != len(set(review_ids)):
        add("duplicate_clean_review_id", "clean_reviews", "clean review ids must be unique")
    review_step_owners: dict[str, set[str]] = defaultdict(set)
    for review in ledger.clean_reviews:
        path = f"clean_reviews.{review.id}"
        state_evidence_ids = [item.state_node_id for item in review.state_evidence]
        review_nodes = set(state_evidence_ids)
        mapped_step_ids = [
            step_id
            for item in review.state_evidence
            for step_id in item.evidence_step_ids
        ]
        for step_id in set(mapped_step_ids):
            review_step_owners[step_id].add(review.id)
        if not review.started_from_clean:
            add("clean_review_dirty_start", path, "review did not start from a clean state")
        if len(state_evidence_ids) != len(review_nodes):
            add(
                "clean_review_duplicate_state_evidence",
                f"{path}.state_evidence",
                "each state may appear only once in a clean review evidence mapping",
            )
        if review_nodes != all_nodes:
            add(
                "clean_review_state_evidence_incomplete",
                f"{path}.state_evidence",
                "per-state evidence mapping must cover every state node exactly",
            )
        if len(mapped_step_ids) != len(set(mapped_step_ids)):
            add(
                "clean_review_step_reused_across_states",
                f"{path}.state_evidence",
                "one evidence step cannot prove review coverage for multiple states",
            )
        if review.reviewed_state_node_ids and set(review.reviewed_state_node_ids) != review_nodes:
            add(
                "clean_review_flat_coverage_mismatch",
                f"{path}.reviewed_state_node_ids",
                "compatibility state ids must equal the per-state evidence mapping",
            )
        if review.evidence_step_ids and set(review.evidence_step_ids) != set(mapped_step_ids):
            add(
                "clean_review_flat_evidence_mismatch",
                f"{path}.evidence_step_ids",
                "compatibility evidence ids must equal the per-state evidence mapping",
            )
        if review.new_safe_candidate_ids:
            add(
                "clean_review_found_candidates",
                f"{path}.new_safe_candidate_ids",
                "review found new safe candidates and therefore is not clean",
            )
        if not review.passed:
            add("clean_review_failed", path, "clean review is not marked passed")
        if (
            review.started_from_clean
            and review_nodes == all_nodes
            and len(state_evidence_ids) == len(review_nodes)
            and len(mapped_step_ids) == len(set(mapped_step_ids))
            and not review.new_safe_candidate_ids
            and review.passed
        ):
            complete_clean_reviews.append(review)

    for step_id, review_owners in sorted(review_step_owners.items()):
        if len(review_owners) > 1:
            add(
                "clean_review_step_reused_across_reviews",
                "clean_reviews",
                f"{step_id} is reused by clean reviews {sorted(review_owners)}",
            )

    independent_run_ids = {item.evidence_run_id for item in complete_clean_reviews}
    if len(complete_clean_reviews) < 2 or len(independent_run_ids) < 2:
        add(
            "insufficient_independent_clean_reviews",
            "clean_reviews",
            "two complete clean reviews with distinct evidence runs are required",
        )

    human = ledger.human_omission_review
    if human is None:
        add(
            "human_omission_review_missing",
            "human_omission_review",
            "human obvious-omission review is required",
        )
    else:
        if set(human.reviewed_state_node_ids) != all_nodes:
            add(
                "human_review_incomplete_coverage",
                "human_omission_review.reviewed_state_node_ids",
                "human review must cover every state node exactly",
            )
        if human.obvious_omission_candidate_ids:
            add(
                "human_review_found_obvious_omission",
                "human_omission_review.obvious_omission_candidate_ids",
                "human review found visible candidates still missing from the ledger",
            )
        if not human.passed:
            add(
                "human_omission_review_failed",
                "human_omission_review.passed",
                "human obvious-omission review did not pass",
            )

    answerability_by_dimension: dict[str, list[DomainAnswerability]] = defaultdict(list)
    for item in ledger.answerability:
        answerability_by_dimension[item.dimension].append(item)
    for dimension in ("mechanics", "resources", "states"):
        entries = answerability_by_dimension[dimension]
        path = f"answerability.{dimension}"
        if len(entries) != 1:
            add(
                "answerability_dimension_invalid",
                path,
                "exactly one answerability declaration is required",
            )
            continue
        entry = entries[0]
        if not entry.answerable:
            add(
                "dimension_not_answerable",
                path,
                f"{dimension} cannot yet be answered from fact data",
            )
        if not entry.checks:
            add(
                "answerability_checks_missing",
                f"{path}.checks",
                "answerable dimension needs at least one evidenced fact query",
            )

    (
        evidence_paths,
        publishable_steps,
        expected_review_runs,
        action_expectations,
    ) = _evidence_refs(ledger)
    resolved_steps: dict[str, EvidenceStep] = {}
    resolved_runs: dict[str, EvidenceRun] = {}
    if evidence_paths and store is None:
        add(
            "evidence_resolver_missing",
            "evidence",
            "evidence ids were supplied but no canonical store was provided",
        )
    elif store is not None:
        for step_id in sorted(evidence_paths):
            step = store.get_evidence_step(step_id)
            if step is None:
                add(
                    "evidence_step_missing",
                    sorted(evidence_paths[step_id])[0],
                    f"evidence step does not resolve: {step_id}",
                )
                continue
            resolved_steps[step_id] = step
            if step.status != "passed":
                add(
                    "evidence_step_not_passed",
                    sorted(evidence_paths[step_id])[0],
                    f"evidence step status is {step.status}: {step_id}",
                )
            for (
                expectation_path,
                expected_action,
                expected_bounds,
                expected_width,
                expected_height,
            ) in action_expectations.get(step_id, []):
                if step.action != expected_action:
                    add(
                        "evidence_action_mismatch",
                        expectation_path,
                        f"{step_id} action does not match the ledger action",
                    )
                if step.target_bounds != expected_bounds:
                    add(
                        "evidence_bounds_mismatch",
                        expectation_path,
                        f"{step_id} target bounds do not match the ledger bounds",
                    )
                if (
                    step.viewport_width != expected_width
                    or step.viewport_height != expected_height
                ):
                    add(
                        "evidence_viewport_mismatch",
                        expectation_path,
                        f"{step_id} viewport does not match the state node",
                    )
            run = resolved_runs.get(step.evidence_run_id)
            if run is None:
                run = store.get_evidence_run(step.evidence_run_id)
                if run is not None:
                    resolved_runs[run.id] = run
            if run is None:
                add(
                    "evidence_run_missing",
                    sorted(evidence_paths[step_id])[0],
                    f"evidence run does not resolve: {step.evidence_run_id}",
                )
                continue
            if run.game_id != ledger.scope.game_id:
                add(
                    "evidence_scope_mismatch",
                    sorted(evidence_paths[step_id])[0],
                    f"evidence game_id disagrees for {step_id}",
                )
            if run.build_scope_id != ledger.scope.build_scope_id:
                add(
                    "evidence_scope_mismatch",
                    sorted(evidence_paths[step_id])[0],
                    f"evidence build_scope_id disagrees for {step_id}",
                )
            if run.scope_id != ledger.scope.id:
                add(
                    "evidence_scope_mismatch",
                    sorted(evidence_paths[step_id])[0],
                    f"evidence scope_id disagrees for {step_id}",
                )
            for expected_path, expected_review_run in expected_review_runs.get(
                step_id, []
            ):
                if expected_review_run != step.evidence_run_id:
                    add(
                        "clean_review_evidence_run_mismatch",
                        expected_path,
                        f"review evidence belongs to {step.evidence_run_id}, "
                        f"not {expected_review_run}",
                    )
            if step_id in publishable_steps:
                publication_issues = step.publication_issues()
                if publication_issues:
                    add(
                        "publishable_interaction_evidence_incomplete",
                        sorted(evidence_paths[step_id])[0],
                        f"{step_id}: " + "; ".join(publication_issues),
                    )
                if run.status != "passed":
                    add(
                        "publishable_interaction_run_incomplete",
                        sorted(evidence_paths[step_id])[0],
                        f"evidence run status is {run.status}: {run.id}",
                    )
        for review in ledger.clean_reviews:
            run = resolved_runs.get(review.evidence_run_id) or store.get_evidence_run(
                review.evidence_run_id
            )
            if run is None:
                add(
                    "clean_review_run_missing",
                    f"clean_reviews.{review.id}.evidence_run_id",
                    f"clean review run does not resolve: {review.evidence_run_id}",
                )
            elif run.status != "passed":
                add(
                    "clean_review_run_incomplete",
                    f"clean_reviews.{review.id}.evidence_run_id",
                    f"clean review run status is {run.status}",
                )

    issues.sort(key=lambda item: (item.code, item.path, item.message))
    saturation_pass = not issues
    return SaturationValidationResult(
        ledger_id=ledger.id,
        ok=saturation_pass,
        saturation_pass=saturation_pass,
        counts={
            "state_nodes": len(ledger.state_nodes),
            "visible_candidates": len(candidate_ids),
            "resolved_evidence_steps": len(resolved_steps),
            "complete_clean_reviews": len(complete_clean_reviews),
            "issues": len(issues),
        },
        unresolved_candidate_ids=sorted(unresolved),
        unreviewed_state_node_ids=sorted(unreviewed),
        issues=issues,
    )


def write_saturation_validation(
    result: SaturationValidationResult,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
