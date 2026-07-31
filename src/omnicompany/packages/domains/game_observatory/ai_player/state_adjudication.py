"""Hash-locked independent review for semantic states and transition edges."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import ArtifactRef
from ..store import ObservatoryStore
from .contracts import (
    SemanticStateV1,
    StateAssignmentV1,
    StateObservationV1,
    TransitionEdgeV1,
)
from .state_graph import SemanticStateGraph
from .store import AIPlayerStore


PACKET_SCHEMA = "game-observatory.ai-player.state-review-packet.v1"
SEED_SCHEMA = "game-observatory.ai-player.state-adjudication-seed.v1"
RESULT_SCHEMA = "game-observatory.ai-player.state-adjudication-result.v1"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _model_sha256(model: BaseModel) -> str:
    return _sha256_bytes(model.model_dump_json(by_alias=True).encode("utf-8"))


def _canonical_bytes(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\
"
    ).encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class StateReviewArtifactV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class StateReviewCandidateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SemanticStateV1
    state_sha256: str = Field(min_length=64, max_length=64)
    assignments: tuple[StateAssignmentV1, ...] = Field(min_length=1)
    observations: tuple[StateObservationV1, ...] = Field(min_length=1)
    artifacts: tuple[StateReviewArtifactV1, ...] = Field(min_length=1)


class TransitionReviewCandidateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge: TransitionEdgeV1
    edge_sha256: str = Field(min_length=64, max_length=64)
    from_state_title: str = Field(min_length=1)
    from_state_status: str = Field(min_length=1)
    to_state_title: str | None = None
    to_state_status: str | None = None
    artifacts: tuple[StateReviewArtifactV1, ...] = Field(min_length=1)


class StateReviewPacketV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.state-review-packet.v1"] = Field(
        default=PACKET_SCHEMA,
        alias="schema",
    )
    packet_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    states: tuple[StateReviewCandidateV1, ...]
    edges: tuple[TransitionReviewCandidateV1, ...]

    @model_validator(mode="after")
    def require_review_work(self) -> "StateReviewPacketV1":
        if not self.states and not self.edges:
            raise ValueError("state review packet has no candidate work")
        return self


class StateAdjudicationDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    expected_state_sha256: str = Field(min_length=64, max_length=64)
    disposition: Literal["accepted", "candidate", "invalidated"]
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    reviewed_observation_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @field_validator("aliases", "tags", "reviewed_observation_ids")
    @classmethod
    def require_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("state adjudication decision values must be unique")
        return value


class TransitionAdjudicationDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    expected_edge_sha256: str = Field(min_length=64, max_length=64)
    outcome: Literal[
        "verified_transition",
        "verified_state_change",
        "verified_no_change",
        "failed",
        "forbidden",
        "deferred",
    ]
    observed_change: str = Field(min_length=1)
    reviewed_evidence_step_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @field_validator("reviewed_evidence_step_ids")
    @classmethod
    def require_unique_steps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("reviewed evidence step ids must be unique")
        return value


class StateAdjudicationSeedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.state-adjudication-seed.v1"] = Field(
        default=SEED_SCHEMA,
        alias="schema",
    )
    seed_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    packet_sha256: str = Field(min_length=64, max_length=64)
    adjudicator_id: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    state_decisions: tuple[StateAdjudicationDecisionV1, ...] = ()
    transition_decisions: tuple[TransitionAdjudicationDecisionV1, ...] = ()

    @model_validator(mode="after")
    def require_unique_decisions(self) -> "StateAdjudicationSeedV1":
        if not self.state_decisions and not self.transition_decisions:
            raise ValueError("state adjudication seed has no decisions")
        state_ids = [item.state_id for item in self.state_decisions]
        edge_ids = [item.edge_id for item in self.transition_decisions]
        if len(state_ids) != len(set(state_ids)):
            raise ValueError("state adjudication decisions must be unique")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("transition adjudication decisions must be unique")
        return self


class StateAdjudicationResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.state-adjudication-result.v1"] = Field(
        default=RESULT_SCHEMA,
        alias="schema",
    )
    seed_id: str = Field(min_length=1)
    seed_sha256: str = Field(min_length=64, max_length=64)
    packet_sha256: str = Field(min_length=64, max_length=64)
    environment_id: str = Field(min_length=1)
    adjudicator_id: str = Field(min_length=1)
    state_version_ids: tuple[str, ...]
    transition_version_ids: tuple[str, ...]
    persistence_reopen_verified: bool


def _review_artifact(observatory: ObservatoryStore, artifact_id: str) -> StateReviewArtifactV1:
    artifact = observatory.get_artifact(artifact_id)
    if artifact is None:
        raise ValueError(f"unknown review artifact: {artifact_id}")
    root = observatory.artifact_root.resolve()
    path = Path(artifact.path).resolve()
    if not _inside(path, root):
        raise ValueError(f"review artifact escapes canonical root: {artifact_id}")
    if not path.is_file():
        raise ValueError(f"review artifact file is missing: {artifact_id}")
    content = path.read_bytes()
    if _sha256_bytes(content) != artifact.sha256:
        raise ValueError(f"review artifact hash mismatch: {artifact_id}")
    return StateReviewArtifactV1(
        id=artifact.id,
        kind=artifact.kind,
        media_type=artifact.media_type,
        path=str(path),
        sha256=artifact.sha256,
        size_bytes=len(content),
    )


def _artifacts_for_ids(
    observatory: ObservatoryStore,
    artifact_ids: list[str],
) -> tuple[StateReviewArtifactV1, ...]:
    return tuple(
        _review_artifact(observatory, artifact_id)
        for artifact_id in dict.fromkeys(artifact_ids)
    )


def build_state_review_packet(
    player: AIPlayerStore,
    environment_id: str,
) -> StateReviewPacketV1:
    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown environment: {environment_id}")
    selection = player.select_environment_lineage(environment_id)
    if selection.selected_environment_id != environment_id:
        raise ValueError("state review requires the current environment leaf")

    states: list[StateReviewCandidateV1] = []
    for state in player.list_semantic_states(environment_id, statuses=("candidate",)):
        assignments = tuple(
            player.list_state_assignments(
                environment_id,
                state_id=state.id,
                latest_only=True,
            )
        )
        if not assignments:
            raise ValueError(f"candidate state has no current assignments: {state.id}")
        observations = tuple(
            observation
            for assignment in assignments
            if (
                observation := player.get_state_observation(
                    environment_id,
                    assignment.observation_id,
                )
            )
            is not None
        )
        if len(observations) != len(assignments):
            raise ValueError(f"candidate state has a missing observation: {state.id}")
        references = [
            reference
            for item in (state, *assignments, *observations)
            for reference in item.evidence_refs
        ]
        player.resolve_evidence_references(references, environment_scope=environment)
        artifact_ids = [
            artifact_id
            for reference in references
            for artifact_id in reference.artifact_ids
        ]
        states.append(
            StateReviewCandidateV1(
                state=state,
                state_sha256=_model_sha256(state),
                assignments=assignments,
                observations=observations,
                artifacts=_artifacts_for_ids(player.observatory_store, artifact_ids),
            )
        )

    edges: list[TransitionReviewCandidateV1] = []
    for edge in player.list_transition_edges(environment_id, outcomes=("deferred",)):
        source = player.get_semantic_state(environment_id, edge.from_state_id)
        destination = (
            player.get_semantic_state(environment_id, edge.to_state_id)
            if edge.to_state_id is not None
            else None
        )
        if source is None or (edge.to_state_id is not None and destination is None):
            raise ValueError(f"deferred edge endpoint is missing: {edge.id}")
        player.resolve_evidence_references(edge.evidence_refs, environment_scope=environment)
        artifact_ids = [
            artifact_id
            for reference in edge.evidence_refs
            for artifact_id in reference.artifact_ids
        ]
        edges.append(
            TransitionReviewCandidateV1(
                edge=edge,
                edge_sha256=_model_sha256(edge),
                from_state_title=source.title,
                from_state_status=source.status,
                to_state_title=destination.title if destination is not None else None,
                to_state_status=destination.status if destination is not None else None,
                artifacts=_artifacts_for_ids(player.observatory_store, artifact_ids),
            )
        )

    identity = {
        "environment_id": environment_id,
        "states": [(item.state.id, item.state_sha256) for item in states],
        "edges": [(item.edge.id, item.edge_sha256) for item in edges],
    }
    digest = _sha256_bytes(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    return StateReviewPacketV1(
        packet_id=f"state-review.{digest[:32]}",
        environment_id=environment_id,
        states=tuple(states),
        edges=tuple(edges),
    )


def export_state_review_packet(
    player: AIPlayerStore,
    environment_id: str,
    output_path: Path,
) -> tuple[StateReviewPacketV1, str]:
    packet = build_state_review_packet(player, environment_id)
    content = _canonical_bytes(packet)
    _write_atomic(output_path, content)
    return packet, _sha256_bytes(content)


def _packet_state(
    packet: StateReviewPacketV1,
    state_id: str,
) -> StateReviewCandidateV1:
    try:
        return next(item for item in packet.states if item.state.id == state_id)
    except StopIteration as error:
        raise ValueError(f"state decision is absent from source packet: {state_id}") from error


def _packet_edge(
    packet: StateReviewPacketV1,
    edge_id: str,
) -> TransitionReviewCandidateV1:
    try:
        return next(item for item in packet.edges if item.edge.id == edge_id)
    except StopIteration as error:
        raise ValueError(f"edge decision is absent from source packet: {edge_id}") from error


def _desired_state(
    current: SemanticStateV1,
    decision: StateAdjudicationDecisionV1,
    seed: StateAdjudicationSeedV1,
) -> SemanticStateV1:
    tags = list(
        dict.fromkeys(
            [
                *decision.tags,
                *(tag for tag in current.tags if tag != "auto-discovered"),
                "independently-adjudicated",
                f"adjudicator:{seed.adjudicator_id}",
                f"adjudication-seed:{seed.seed_id}",
            ]
        )
    )
    payload = current.model_dump()
    payload.update(
        {
            "version": decision.expected_version + 1,
            "title": decision.title,
            "description": decision.description,
            "aliases": list(decision.aliases),
            "tags": tags,
            "status": decision.disposition,
            "supersedes_id": current.id,
            "created_at": seed.reviewed_at,
        }
    )
    return SemanticStateV1.model_validate(payload)


def _desired_edge(
    current: TransitionEdgeV1,
    decision: TransitionAdjudicationDecisionV1,
    seed: StateAdjudicationSeedV1,
) -> TransitionEdgeV1:
    if decision.outcome in {"verified_transition", "verified_state_change"}:
        if current.to_state_id is None or current.from_state_id == current.to_state_id:
            raise ValueError(f"state-changing edge decision has no distinct destination: {current.id}")
    if decision.outcome == "verified_no_change" and (
        current.to_state_id is None or current.from_state_id != current.to_state_id
    ):
        raise ValueError(f"verified-no-change decision has distinct endpoints: {current.id}")
    payload = current.model_dump()
    payload.update(
        {
            "version": decision.expected_version + 1,
            "observed_change": decision.observed_change,
            "outcome": decision.outcome,
            "created_at": seed.reviewed_at,
        }
    )
    return TransitionEdgeV1.model_validate(payload)


def apply_state_adjudication_seed(
    store_root: Path,
    packet_path: Path,
    seed_path: Path,
    result_path: Path,
    *,
    expected_store_root: Path,
    expected_seed_sha256: str,
) -> StateAdjudicationResultV1:
    resolved_root = store_root.resolve()
    if resolved_root != expected_store_root.resolve():
        raise ValueError("AI-player store root mismatch")
    seed_bytes = seed_path.read_bytes()
    seed_sha256 = _sha256_bytes(seed_bytes)
    if seed_sha256 != expected_seed_sha256.lower():
        raise ValueError("state adjudication seed SHA-256 mismatch")
    seed = StateAdjudicationSeedV1.model_validate_json(seed_bytes)
    packet_bytes = packet_path.read_bytes()
    packet_sha256 = _sha256_bytes(packet_bytes)
    if packet_sha256 != seed.packet_sha256:
        raise ValueError("state review packet SHA-256 mismatch")
    packet = StateReviewPacketV1.model_validate_json(packet_bytes)
    if packet.environment_id != seed.environment_id:
        raise ValueError("state review packet environment mismatch")

    observatory = ObservatoryStore(resolved_root)
    player = AIPlayerStore(observatory)
    environment = player.get_environment(seed.environment_id)
    if environment is None:
        raise KeyError(f"unknown environment: {seed.environment_id}")
    state_results: list[SemanticStateV1] = []
    for decision in seed.state_decisions:
        source = _packet_state(packet, decision.state_id)
        if (
            source.state.version != decision.expected_version
            or source.state_sha256 != decision.expected_state_sha256
        ):
            raise ValueError(f"state decision does not match source packet: {decision.state_id}")
        packet_observation_ids = tuple(
            item.observation_id for item in source.assignments
        )
        if set(decision.reviewed_observation_ids) != set(packet_observation_ids):
            raise ValueError(
                f"state decision must review every current observation: {decision.state_id}"
            )
        current = player.get_semantic_state(seed.environment_id, decision.state_id)
        if current is None:
            raise ValueError(f"state decision target is missing: {decision.state_id}")
        desired = _desired_state(source.state, decision, seed)
        if current == desired:
            state_results.append(current)
            continue
        if current.version != decision.expected_version or _model_sha256(current) != source.state_sha256:
            raise ValueError(f"state decision target changed after review: {decision.state_id}")
        current_assignments = player.list_state_assignments(
            seed.environment_id,
            state_id=current.id,
            latest_only=True,
        )
        if {item.observation_id for item in current_assignments} != set(packet_observation_ids):
            raise ValueError(f"state assignments changed after review: {decision.state_id}")
        state_results.append(player.put_semantic_state(desired))

    edge_results: list[TransitionEdgeV1] = []
    graph = SemanticStateGraph(player)
    for decision in seed.transition_decisions:
        source = _packet_edge(packet, decision.edge_id)
        if (
            source.edge.version != decision.expected_version
            or source.edge_sha256 != decision.expected_edge_sha256
        ):
            raise ValueError(f"edge decision does not match source packet: {decision.edge_id}")
        packet_step_ids = {
            step_id
            for reference in source.edge.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        if set(decision.reviewed_evidence_step_ids) != packet_step_ids:
            raise ValueError(f"edge decision must review every EvidenceStep: {decision.edge_id}")
        current = player.get_transition_edge(seed.environment_id, decision.edge_id)
        if current is None:
            raise ValueError(f"edge decision target is missing: {decision.edge_id}")
        desired = _desired_edge(source.edge, decision, seed)
        if current == desired:
            edge_results.append(current)
            continue
        if current.version != decision.expected_version or _model_sha256(current) != source.edge_sha256:
            raise ValueError(f"edge decision target changed after review: {decision.edge_id}")
        if decision.outcome in {"verified_transition", "verified_state_change", "verified_no_change"}:
            endpoint_ids = [current.from_state_id, current.to_state_id]
            endpoints = [
                player.get_semantic_state(seed.environment_id, state_id)
                for state_id in endpoint_ids
                if state_id is not None
            ]
            if len(endpoints) != len(endpoint_ids) or any(
                item is None or item.status != "accepted" for item in endpoints
            ):
                raise ValueError(f"verified edge requires accepted endpoints: {decision.edge_id}")
        edge_results.append(graph.put_edge(desired))

    reopened = AIPlayerStore(ObservatoryStore(resolved_root))
    persistence_verified = all(
        reopened.get_semantic_state(seed.environment_id, item.id) == item
        for item in state_results
    ) and all(
        reopened.get_transition_edge(seed.environment_id, item.id) == item
        for item in edge_results
    )
    result = StateAdjudicationResultV1(
        seed_id=seed.seed_id,
        seed_sha256=seed_sha256,
        packet_sha256=packet_sha256,
        environment_id=seed.environment_id,
        adjudicator_id=seed.adjudicator_id,
        state_version_ids=tuple(f"{item.id}@{item.version}" for item in state_results),
        transition_version_ids=tuple(f"{item.id}@{item.version}" for item in edge_results),
        persistence_reopen_verified=persistence_verified,
    )
    _write_atomic(result_path, _canonical_bytes(result))
    if not persistence_verified:
        raise RuntimeError("state adjudication persistence verification failed")
    return result