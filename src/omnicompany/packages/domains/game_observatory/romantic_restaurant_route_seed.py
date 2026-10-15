# [OMNI] origin=codex created_by=codex intent="seed an evidence-bound guarded Romantic Restaurant route"
"""Canonical Romantic Restaurant seed for the first guarded high-repetition route.

The seed deliberately records only the two observed snapshots, their evidence-backed
single-tap transition, and an unvalidated ten-action candidate skill.  It does not
promote the route, claim benchmark eligibility, or perform any device action.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ai_player.contracts import (
    EvidenceReferenceV1,
    SemanticStateV1,
    SkillLocatorV1,
    SkillStepV1,
    SkillVersionV1,
    TransitionEdgeV1,
)
from .ai_player.skills import applicability_scope_from_environment, build_skill_version
from .ai_player.store import AIPlayerStore
from .models import NormalizedAction, SourcePixelRect

ENVIRONMENT_ID = "environment.romantic-restaurant.mumu0.preidentity.3_95_0"
BEFORE_ARTIFACT_ID = "art.device.1785921087171.059e9b6e"
AFTER_ARTIFACT_ID = "art.probe-terminal.1785921090924.db35395e"
EVIDENCE_RUN_ID = "evidence.run.c352ee263e3d4a94beac321aaed49fc7"
EVIDENCE_STEP_ID = "evidence.step.a224f8b376ef4cb890a91635655ab14c"
ACTION_RUN_ID = "run.action.1785921088575.2bb642e1"
BEFORE_STATE_ID = "state.romantic.shopping-basket.before.round-2"
AFTER_STATE_ID = "state.romantic.shopping-basket.after.round-2"
TRANSITION_ID = "transition.romantic.shopping-basket.single-generation.round-2"
SKILL_ID = "skill.romantic.shopping-basket-ten-tap.guard-v1"
SKILL_VERSION_ID = "skill-version.romantic.shopping-basket-ten-tap.guard-v1.1"
LOCATOR_ID = "locator.rr.shopping-basket.selected.round-2"
CREATED_AT = "2026-08-05T09:11:31.288628+00:00"


@dataclass(frozen=True)
class RomanticRestaurantRouteSeedResult:
    before_state: SemanticStateV1
    after_state: SemanticStateV1
    transition: TransitionEdgeV1
    skill: SkillVersionV1
    inserted_state_count: int
    inserted_transition_count: int
    inserted_skill_count: int


def _evidence_reference(
    *,
    artifact_ids: list[str],
    note: str,
    include_receipts: bool = True,
) -> EvidenceReferenceV1:
    return EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=artifact_ids,
        evidence_run_ids=[EVIDENCE_RUN_ID] if include_receipts else [],
        evidence_step_ids=[EVIDENCE_STEP_ID] if include_receipts else [],
        trace_run_ids=[ACTION_RUN_ID] if include_receipts else [],
        note=note,
    )


def build_romantic_restaurant_route_seed(
    player: AIPlayerStore,
) -> tuple[SemanticStateV1, SemanticStateV1, TransitionEdgeV1, SkillVersionV1]:
    """Build the deterministic seed from already-canonical evidence."""

    environment = player.get_environment(ENVIRONMENT_ID)
    if environment is None:
        raise KeyError(f"unknown AI-player environment: {ENVIRONMENT_ID}")

    observatory = player.observatory_store
    before_artifact = observatory.get_artifact(BEFORE_ARTIFACT_ID)
    after_artifact = observatory.get_artifact(AFTER_ARTIFACT_ID)
    evidence_run = observatory.get_evidence_run(EVIDENCE_RUN_ID)
    evidence_step = observatory.get_evidence_step(EVIDENCE_STEP_ID)
    action_run = observatory.get_run(ACTION_RUN_ID)
    missing = [
        name
        for name, value in (
            (BEFORE_ARTIFACT_ID, before_artifact),
            (AFTER_ARTIFACT_ID, after_artifact),
            (EVIDENCE_RUN_ID, evidence_run),
            (EVIDENCE_STEP_ID, evidence_step),
            (ACTION_RUN_ID, action_run),
        )
        if value is None
    ]
    if missing:
        raise ValueError("canonical route seed evidence is missing: " + ", ".join(missing))
    assert before_artifact is not None
    assert after_artifact is not None
    assert evidence_run is not None
    assert evidence_step is not None
    assert action_run is not None

    if before_artifact.sha256 is None or after_artifact.sha256 is None:
        raise ValueError("canonical route seed screenshots require SHA-256 digests")
    if evidence_step.evidence_run_id != EVIDENCE_RUN_ID:
        raise ValueError("canonical EvidenceStep does not belong to the declared EvidenceRun")
    if evidence_step.action_run_id != ACTION_RUN_ID:
        raise ValueError("canonical EvidenceStep does not bind the declared ActionRun")
    if evidence_step.before_frame_id != BEFORE_ARTIFACT_ID:
        raise ValueError("canonical EvidenceStep before frame does not match the route seed")
    if evidence_step.after_frame_id != AFTER_ARTIFACT_ID:
        raise ValueError("canonical EvidenceStep after frame does not match the route seed")
    if evidence_step.action.type != "tap" or (evidence_step.action.x, evidence_step.action.y) != (
        404,
        1120,
    ):
        raise ValueError("canonical EvidenceStep action geometry does not match the route seed")
    if evidence_step.target_bounds != SourcePixelRect(x=340, y=1050, width=130, height=150):
        raise ValueError("canonical EvidenceStep target bounds do not match the route seed")

    before_ref = _evidence_reference(
        artifact_ids=[BEFORE_ARTIFACT_ID],
        note=(
            "Snapshot immediately before the confirmed shopping-basket tap. "
            "This is a local board snapshot, not a complete game-state model."
        ),
    )
    after_ref = _evidence_reference(
        artifact_ids=[AFTER_ARTIFACT_ID],
        note=(
            "Stable terminal snapshot after the confirmed shopping-basket tap. "
            "Human business review confirmed energy 100 to 99 and one new base item."
        ),
    )
    transition_ref = _evidence_reference(
        artifact_ids=[BEFORE_ARTIFACT_ID, AFTER_ARTIFACT_ID],
        note=(
            "Human business review confirmed energy 100 to 99 and one new base item. "
            "The historical visual evaluator remained expectation_met=false, "
            "stop_recommended=true; that conflict is intentionally preserved."
        ),
    )

    before_state = SemanticStateV1(
        id=BEFORE_STATE_ID,
        environment_id=ENVIRONMENT_ID,
        title="Shopping basket ready before one generation",
        description=(
            "Observed board snapshot with the selected level-5 shopping basket ready for "
            "one low-risk generation action. The snapshot does not assert complete board rules."
        ),
        semantic_fingerprint="romantic.shopping-basket.ready.before.round-2",
        observation_feature_hashes=[before_artifact.sha256],
        aliases=["romantic-shopping-basket-before-round-2"],
        tags=["romantic-restaurant", "merge-board", "generator-ready", "snapshot"],
        status="accepted",
        evidence_refs=[before_ref],
        created_at=CREATED_AT,
    )
    after_state = SemanticStateV1(
        id=AFTER_STATE_ID,
        environment_id=ENVIRONMENT_ID,
        title="Shopping basket terminal state after one generation",
        description=(
            "Observed stable terminal snapshot after one shopping-basket tap. Human review "
            "confirmed one energy consumed and one base item added; no broader rule is implied."
        ),
        semantic_fingerprint="romantic.shopping-basket.generated.after.round-2",
        observation_feature_hashes=[after_artifact.sha256],
        aliases=["romantic-shopping-basket-after-round-2"],
        tags=["romantic-restaurant", "merge-board", "generated-item", "snapshot"],
        status="accepted",
        evidence_refs=[after_ref],
        created_at=CREATED_AT,
    )
    transition = TransitionEdgeV1(
        id=TRANSITION_ID,
        environment_id=ENVIRONMENT_ID,
        from_state_id=BEFORE_STATE_ID,
        to_state_id=AFTER_STATE_ID,
        action=NormalizedAction(type="tap", x=404, y=1120),
        target_bounds=SourcePixelRect(x=340, y=1050, width=130, height=150),
        expected_change="Consume one energy and generate one base item in an empty board cell.",
        observed_change=(
            "Human business review confirmed energy changed from 100 to 99 and one base item "
            "appeared on the board. The historical visual evaluator did not meet its threshold "
            "and recommended stopping; the conflicting evaluator result remains unchanged."
        ),
        outcome="verified_transition",
        evidence_refs=[transition_ref],
        created_at=CREATED_AT,
    )

    steps = [
        SkillStepV1(
            id=f"step.romantic.shopping-basket-generation.{sequence:02d}",
            kind="action",
            depends_on_step_ids=(
                []
                if sequence == 1
                else [f"step.romantic.shopping-basket-generation.{sequence - 1:02d}"]
            ),
            action=NormalizedAction(type="tap", x=404, y=1120),
            locator_id=LOCATOR_ID,
            max_attempts=1,
            idempotency="verify_before_retry",
            side_effect="progression",
        )
        for sequence in range(1, 11)
    ]
    skill = build_skill_version(
        environment_id=ENVIRONMENT_ID,
        id=SKILL_VERSION_ID,
        skill_id=SKILL_ID,
        creator_id="game-observatory.game-modeling",
        version=1,
        level="L2",
        skill_layer="flow",
        scope="gameplay",
        execution_mode="planned",
        perception_tier="P1",
        title="Guarded ten-tap shopping-basket generation",
        applicability=(
            "Only for the exact Romantic Restaurant 3.95.0 environment when the source-state "
            "guard confirms the selected shopping basket, sufficient energy, and ten empty cells."
        ),
        applicability_scope=applicability_scope_from_environment(
            environment,
            required_state_ids=[BEFORE_STATE_ID],
        ),
        safety_level="progression",
        parameters_schema={},
        preconditions=[
            "The guarded batch contract is executable and has unused single-live-validation authorization.",
            "The canonical session is running with an active task, live lease, and at least 11 remaining actions.",
            "Live preflight confirms the reference source, at least 10 energy, at least 10 empty cells, and no forbidden surface.",
        ],
        executor_kind="normalized_actions",
        executor_ref=(
            "omnicompany.packages.domains.game_observatory.ai_player."
            "guarded_batch_route:execute_guarded_batch_route"
        ),
        procedure_steps=[
            f"Guard, execute, evaluate, and retain receipt for shopping-basket tap {sequence}."
            for sequence in range(1, 11)
        ],
        locators=[
            SkillLocatorV1(
                id=LOCATOR_ID,
                step_index=1,
                strategy="source_pixel",
                selector="404,1120",
                reference_bounds=SourcePixelRect(x=340, y=1050, width=130, height=150),
                mobility="fixed_surface",
                reference_artifact_id=BEFORE_ARTIFACT_ID,
            )
        ],
        steps=steps,
        success_checks=[
            "All ten actions have canonical EvidenceStep and ActionRun receipts.",
            "Every counted action has an independently confirmed business-state change.",
            "The batch checkpoint completes without safety violation or pending action.",
        ],
        failure_checks=[
            "Stop on source mismatch, no-change, expectation miss, evaluator stop recommendation, or execution exception.",
            "Stop on insufficient energy, insufficient empty cells, generator not ready, forbidden surface, or lease loss.",
        ],
        recovery_skill_version_ids=[],
        source_transition_ids=[TRANSITION_ID],
        status="candidate",
        validation_run_ids=[],
        evidence_refs=[transition_ref],
        created_at=CREATED_AT,
    )
    return before_state, after_state, transition, skill


def persist_romantic_restaurant_route_seed(
    player: AIPlayerStore,
) -> RomanticRestaurantRouteSeedResult:
    """Idempotently persist the deterministic seed without touching a device."""

    before_state, after_state, transition, skill = build_romantic_restaurant_route_seed(player)
    existing_before = player.get_semantic_state(ENVIRONMENT_ID, BEFORE_STATE_ID)
    existing_after = player.get_semantic_state(ENVIRONMENT_ID, AFTER_STATE_ID)
    existing_transition = player.get_transition_edge(ENVIRONMENT_ID, TRANSITION_ID)
    existing_skill = player.get_skill_version_by_id(ENVIRONMENT_ID, SKILL_VERSION_ID)

    player.put_semantic_state(before_state)
    player.put_semantic_state(after_state)
    player.put_transition_edge(transition)

    if existing_skill is None:
        player.append_skill_version(skill)
    elif existing_skill != skill:
        if existing_skill.content_sha256 != skill.content_sha256:
            raise ValueError(
                "candidate skill id already exists with different executable content: "
                f"{SKILL_VERSION_ID}"
            )
        raise ValueError(
            "candidate skill id already exists with different immutable metadata: "
            f"{SKILL_VERSION_ID}"
        )

    return RomanticRestaurantRouteSeedResult(
        before_state=before_state,
        after_state=after_state,
        transition=transition,
        skill=skill,
        inserted_state_count=int(existing_before is None) + int(existing_after is None),
        inserted_transition_count=int(existing_transition is None),
        inserted_skill_count=int(existing_skill is None),
    )
