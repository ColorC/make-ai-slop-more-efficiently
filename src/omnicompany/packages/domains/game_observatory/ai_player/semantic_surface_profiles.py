"""Shared binding and conflict checks for reusable semantic UI surface profiles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal

from ..models import ArtifactRef, EvidenceStep
from .contracts import SemanticSurfaceProfileV1, StateObservationV1

if TYPE_CHECKING:
    from .store import AIPlayerStore


_PROFILES_KEY = "semantic_surface_profiles"
_PROVENANCE_KEY = "semantic_surface_profile_provenance"


def _validated_pair(
    raw: object,
    *,
    step_id: str,
) -> dict[str, SemanticSurfaceProfileV1]:
    if not isinstance(raw, Mapping) or set(raw) != {"before", "after"}:
        raise ValueError(
            "EvidenceStep semantic_surface_profiles must contain exactly "
            f"before and after roles: {step_id}"
        )
    return {
        role: SemanticSurfaceProfileV1.model_validate(raw[role])
        for role in ("before", "after")
    }


def _require_provenance(raw: object, *, source: str) -> None:
    if not isinstance(raw, Mapping) or set(raw) != {"producer", "actor"}:
        raise ValueError(f"semantic surface profile provenance is invalid: {source}")
    if any(not isinstance(raw[key], str) or not raw[key].strip() for key in raw):
        raise ValueError(f"semantic surface profile provenance is invalid: {source}")


def attach_semantic_surface_profiles(
    step: EvidenceStep,
    *,
    before: SemanticSurfaceProfileV1,
    after: SemanticSurfaceProfileV1,
    producer: str,
    actor: str,
) -> EvidenceStep:
    """Bind a complete pair to one terminal step without overwriting disagreement."""

    if not producer.strip() or not actor.strip():
        raise ValueError("semantic surface profile provenance must be non-empty")
    pair = {
        "before": before.model_dump(mode="json", by_alias=True),
        "after": after.model_dump(mode="json", by_alias=True),
    }
    provenance = {"producer": producer, "actor": actor}
    existing_pair = step.metadata.get(_PROFILES_KEY)
    if existing_pair is not None:
        validated_existing = _validated_pair(existing_pair, step_id=step.id)
        if validated_existing != {"before": before, "after": after}:
            raise ValueError(
                f"EvidenceStep already has different semantic surface profiles: {step.id}"
            )
    existing_provenance = step.metadata.get(_PROVENANCE_KEY)
    if existing_provenance is not None:
        _require_provenance(existing_provenance, source=f"step:{step.id}")
        if existing_provenance != provenance:
            raise ValueError(
                f"EvidenceStep already has different semantic surface provenance: {step.id}"
            )
    return step.model_copy(
        update={
            "metadata": {
                **step.metadata,
                _PROFILES_KEY: pair,
                _PROVENANCE_KEY: provenance,
            }
        }
    )


def resolve_semantic_surface_profile(
    step: EvidenceStep,
    artifacts: Sequence[ArtifactRef],
    *,
    role: Literal["before", "after", "observation"],
) -> SemanticSurfaceProfileV1 | None:
    """Resolve one explicit role profile; never infer semantics from UI content."""

    canonical_role = "after" if role == "observation" else role
    candidates: list[tuple[str, SemanticSurfaceProfileV1]] = []
    raw_profiles = step.metadata.get(_PROFILES_KEY)
    if raw_profiles is not None:
        profiles = _validated_pair(raw_profiles, step_id=step.id)
        _require_provenance(
            step.metadata.get(_PROVENANCE_KEY),
            source=f"step:{step.id}",
        )
        candidates.append(
            (
                f"step:{step.id}:{canonical_role}",
                profiles[canonical_role],
            )
        )
    for artifact in artifacts:
        raw_profile = artifact.metadata.get("semantic_surface_profile")
        if raw_profile is not None:
            _require_provenance(
                artifact.metadata.get(_PROVENANCE_KEY),
                source=f"artifact:{artifact.id}",
            )
            candidates.append(
                (
                    f"artifact:{artifact.id}",
                    SemanticSurfaceProfileV1.model_validate(raw_profile),
                )
            )
    if not candidates:
        return None
    expected = candidates[0][1]
    conflicts = [source for source, profile in candidates[1:] if profile != expected]
    if conflicts:
        raise ValueError(
            "conflicting semantic surface profiles for "
            f"{step.id}:{canonical_role}: {candidates[0][0]} vs {', '.join(conflicts)}"
        )
    return expected


def semantic_surface_profile_from_observation(
    observation: StateObservationV1,
) -> SemanticSurfaceProfileV1 | None:
    """Recover only explicitly persisted surface semantics from one observation."""

    features = observation.features
    if not features.page_identity_tokens:
        return None
    return SemanticSurfaceProfileV1(
        page_identity_tokens=features.page_identity_tokens,
        dynamic_field_names=features.dynamic_field_names,
        interaction_roles=features.interaction_roles,
        safe_exit_tokens=features.safe_exit_tokens,
        risk_boundary_tokens=features.risk_boundary_tokens,
    )


def resolve_canonical_state_surface_profile(
    player: AIPlayerStore,
    environment_id: str,
    *,
    observation_id: str | None = None,
    state_id: str | None = None,
) -> SemanticSurfaceProfileV1 | None:
    """Resolve the latest active canonical profile without rereading raw UI text."""

    if observation_id is not None:
        observation = player.get_state_observation(environment_id, observation_id)
        if observation is None:
            raise ValueError(f"semantic surface observation is missing: {observation_id}")
        assignment = player.get_current_state_assignment(environment_id, observation_id)
        if assignment is None or assignment.status != "active":
            raise ValueError(
                "semantic surface observation lacks an active state assignment: "
                f"{observation_id}"
            )
        if state_id is not None and assignment.state_id != state_id:
            raise ValueError(
                "semantic surface observation belongs to another state: "
                f"{assignment.state_id} != {state_id}"
            )
        state = player.get_semantic_state(environment_id, assignment.state_id)
        state_profile = state.surface_profile if state is not None else None
        observation_profile = semantic_surface_profile_from_observation(observation)
        if (
            state_profile is not None
            and observation_profile is not None
            and state_profile != observation_profile
        ):
            raise ValueError(
                "semantic state and exact observation disagree on surface profile: "
                f"{assignment.state_id}:{observation_id}"
            )
        return state_profile or observation_profile

    if state_id is None:
        return None
    state = player.get_semantic_state(environment_id, state_id)
    if state is None:
        raise ValueError(f"semantic surface state is missing: {state_id}")
    if state.surface_profile is not None:
        return state.surface_profile
    candidates: list[tuple[str, str, StateObservationV1]] = []
    for assignment in player.list_state_assignments(
        environment_id,
        state_id=state_id,
        latest_only=True,
    ):
        if assignment.status != "active":
            continue
        observation = player.get_state_observation(environment_id, assignment.observation_id)
        if observation is None or semantic_surface_profile_from_observation(observation) is None:
            continue
        candidates.append((observation.captured_at, assignment.created_at, observation))
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: (item[0], item[1], item[2].id))[2]
    return semantic_surface_profile_from_observation(latest)


def resolve_reusable_semantic_surface_profile(
    player: AIPlayerStore,
    environment_id: str,
    *,
    step: EvidenceStep | None = None,
    artifacts: Sequence[ArtifactRef] = (),
    role: Literal["before", "after", "observation"] = "observation",
    observation_id: str | None = None,
    state_id: str | None = None,
) -> SemanticSurfaceProfileV1 | None:
    """Coalesce evidence and canonical-state profiles, rejecting disagreement."""

    evidence_profile = (
        resolve_semantic_surface_profile(step, artifacts, role=role)
        if step is not None
        else None
    )
    state_profile = resolve_canonical_state_surface_profile(
        player,
        environment_id,
        observation_id=observation_id,
        state_id=state_id,
    )
    if (
        evidence_profile is not None
        and state_profile is not None
        and evidence_profile != state_profile
    ):
        raise ValueError(
            "evidence and canonical state disagree on semantic surface profile: "
            f"{step.id if step is not None else state_id}"
        )
    return evidence_profile or state_profile


def resolve_reusable_semantic_surface_profile_pair(
    player: AIPlayerStore,
    environment_id: str,
    *,
    source_step: EvidenceStep,
    source_artifacts: Sequence[ArtifactRef],
    source_observation_id: str | None = None,
    source_state_id: str | None = None,
    expected_state_id: str | None = None,
) -> tuple[SemanticSurfaceProfileV1, SemanticSurfaceProfileV1] | None:
    """Return a complete replay boundary pair or preserve legacy no-profile behavior."""

    before = resolve_reusable_semantic_surface_profile(
        player,
        environment_id,
        step=source_step,
        artifacts=source_artifacts,
        role="after",
        observation_id=source_observation_id,
        state_id=source_state_id,
    )
    after = resolve_canonical_state_surface_profile(
        player,
        environment_id,
        state_id=expected_state_id,
    )
    if before is None or after is None:
        return None
    return before, after


__all__ = [
    "attach_semantic_surface_profiles",
    "resolve_canonical_state_surface_profile",
    "resolve_reusable_semantic_surface_profile",
    "resolve_reusable_semantic_surface_profile_pair",
    "resolve_semantic_surface_profile",
    "semantic_surface_profile_from_observation",
]
