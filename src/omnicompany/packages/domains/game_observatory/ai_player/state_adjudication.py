"""Hash-locked independent review for semantic states and transition edges."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import EvidenceRun, EvidenceStep
from ..store import ObservatoryStore
from .contracts import (
    PlayerSoftSignalReviewerRole,
    PlayerSoftSignalReviewTrustScope,
    SemanticStateV1,
    SemanticSurfaceAnchorV1,
    SemanticSurfaceProfileV1,
    StateAssignmentV1,
    StateObservationV1,
    TransitionEdgeV1,
)
from .soft_signal_attestation import PlayerSoftSignalReviewerPublicKeyV1
from .state_recognition import critical_feature_conflicts
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
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _unique_evidence_refs(items):
    unique = {}
    for item in items:
        unique.setdefault(item.model_dump_json(by_alias=True), item)
    return list(unique.values())


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
    media_type: str | None = Field(default=None, min_length=1)
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


class StateReviewEvidenceStepV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: EvidenceStep
    step_sha256: str = Field(min_length=64, max_length=64)
    artifacts: tuple[StateReviewArtifactV1, ...] = Field(min_length=1)


class StateReviewEvidenceRunV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run: EvidenceRun
    run_sha256: str = Field(min_length=64, max_length=64)
    steps: tuple[StateReviewEvidenceStepV1, ...] = Field(min_length=1)
    artifacts: tuple[StateReviewArtifactV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_complete_ordered_run(self) -> "StateReviewEvidenceRunV1":
        if tuple(item.step.id for item in self.steps) != tuple(self.run.step_ids):
            raise ValueError("state review evidence run must contain every ordered step")
        if any(item.step.evidence_run_id != self.run.id for item in self.steps):
            raise ValueError("state review evidence step belongs to another run")
        return self


class StateReviewScopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["scoped"] = "scoped"
    parent_packet_id: str = Field(min_length=1)
    parent_packet_sha256: str = Field(min_length=64, max_length=64)
    requested_state_ids: tuple[str, ...] = ()
    requested_edge_ids: tuple[str, ...] = ()
    decision_state_ids: tuple[str, ...]
    decision_edge_ids: tuple[str, ...]
    endpoint_state_ids: tuple[str, ...]
    merge_target_state_ids: tuple[str, ...] = ()
    context_state_ids: tuple[str, ...] = ()
    context_edge_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_closed_unique_scope(self) -> "StateReviewScopeV1":
        fields = (
            "requested_state_ids",
            "requested_edge_ids",
            "decision_state_ids",
            "decision_edge_ids",
            "endpoint_state_ids",
            "merge_target_state_ids",
            "context_state_ids",
            "context_edge_ids",
        )
        for field_name in fields:
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"state review scope {field_name} must be unique")
        if not self.requested_state_ids and not self.requested_edge_ids:
            raise ValueError("scoped state review requires an explicit state or edge")
        if not set(self.requested_state_ids).issubset(self.decision_state_ids):
            raise ValueError("requested states must remain decision states")
        if not set(self.requested_edge_ids).issubset(self.decision_edge_ids):
            raise ValueError("requested edges must remain decision edges")
        if set(self.decision_state_ids) & set(self.context_state_ids):
            raise ValueError("decision and context states must be disjoint")
        if set(self.merge_target_state_ids) & set(self.decision_state_ids):
            raise ValueError("accepted merge targets cannot be decision states")
        if not set(self.merge_target_state_ids).issubset(self.context_state_ids):
            raise ValueError("accepted merge targets must be hash-locked context states")
        if set(self.decision_edge_ids) & set(self.context_edge_ids):
            raise ValueError("decision and context edges must be disjoint")
        if not set(self.endpoint_state_ids).issubset(
            {*self.decision_state_ids, *self.context_state_ids}
        ):
            raise ValueError("every scoped edge endpoint must be included")
        return self


class StateReviewPacketV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["game-observatory.ai-player.state-review-packet.v1"] = Field(
        default=PACKET_SCHEMA,
        alias="schema",
    )
    packet_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    evidence_run_ids: tuple[str, ...] = Field(min_length=1)
    subject_session_ids: tuple[str, ...] = Field(min_length=1)
    states: tuple[StateReviewCandidateV1, ...]
    edges: tuple[TransitionReviewCandidateV1, ...]
    context_states: tuple[StateReviewCandidateV1, ...] = ()
    context_edges: tuple[TransitionReviewCandidateV1, ...] = ()
    evidence_runs: tuple[StateReviewEvidenceRunV1, ...] = ()
    scope: StateReviewScopeV1 | None = None

    @model_validator(mode="after")
    def require_review_work(self) -> "StateReviewPacketV1":
        if not self.states and not self.edges:
            raise ValueError("state review packet has no candidate work")
        if len(self.evidence_run_ids) != len(set(self.evidence_run_ids)):
            raise ValueError("state review packet EvidenceRun ids must be unique")
        if len(self.subject_session_ids) != len(set(self.subject_session_ids)):
            raise ValueError("state review packet subject sessions must be unique")
        run_ids = tuple(item.run.id for item in self.evidence_runs)
        if self.evidence_runs and run_ids != self.evidence_run_ids:
            raise ValueError("state review packet EvidenceRun payloads do not match ids")
        if self.scope is not None:
            if tuple(item.state.id for item in self.states) != self.scope.decision_state_ids:
                raise ValueError("scoped packet decision states do not match its scope")
            if tuple(item.edge.id for item in self.edges) != self.scope.decision_edge_ids:
                raise ValueError("scoped packet decision edges do not match its scope")
            if (
                tuple(item.state.id for item in self.context_states)
                != self.scope.context_state_ids
            ):
                raise ValueError("scoped packet context states do not match its scope")
            if (
                tuple(item.edge.id for item in self.context_edges)
                != self.scope.context_edge_ids
            ):
                raise ValueError("scoped packet context edges do not match its scope")
        return self


class StateAdjudicationDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    expected_state_sha256: str = Field(min_length=64, max_length=64)
    disposition: Literal["accepted", "candidate", "invalidated", "superseded"]
    merge_into_state_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    surface_profile: SemanticSurfaceProfileV1 | None = None
    surface_anchors: tuple[SemanticSurfaceAnchorV1, ...] | None = None
    reviewed_observation_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @field_validator("aliases", "tags", "reviewed_observation_ids")
    @classmethod
    def require_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("state adjudication decision values must be unique")
        return value

    @model_validator(mode="after")
    def require_merge_target(self) -> "StateAdjudicationDecisionV1":
        if self.disposition == "superseded" and self.merge_into_state_id is None:
            raise ValueError("superseded state decision requires merge_into_state_id")
        if self.disposition != "superseded" and self.merge_into_state_id is not None:
            raise ValueError("only superseded state decisions may name merge_into_state_id")
        if self.merge_into_state_id == self.state_id:
            raise ValueError("state decision cannot merge into itself")
        if self.surface_anchors is not None:
            anchor_ids = [anchor.id for anchor in self.surface_anchors]
            if len(anchor_ids) != len(set(anchor_ids)):
                raise ValueError("state adjudication surface anchor ids must be unique")
        return self


class TransitionAdjudicationDecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    expected_edge_sha256: str = Field(min_length=64, max_length=64)
    semantic_observation: Literal[
        "distinct_state",
        "same_state_progress",
        "same_state_no_progress",
        "uncertain",
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


class StateAdjudicationAttestationV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.state-adjudication-attestation.v1"
    ] = Field(
        default="game-observatory.ai-player.state-adjudication-attestation.v1",
        alias="schema",
    )
    reviewer_id: str = Field(min_length=1)
    reviewer_role: PlayerSoftSignalReviewerRole
    reviewer_session_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    payload_sha256: str = Field(min_length=64, max_length=64)
    signature_base64: str = Field(min_length=1)
    issued_at: str = Field(min_length=1)


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
    reviewer_role: PlayerSoftSignalReviewerRole
    trust_scope: PlayerSoftSignalReviewTrustScope = "formal_external"
    reviewer_session_id: str = Field(min_length=1)
    subject_session_ids: tuple[str, ...] = Field(min_length=1)
    evidence_run_ids: tuple[str, ...] = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    state_decisions: tuple[StateAdjudicationDecisionV1, ...] = ()
    transition_decisions: tuple[TransitionAdjudicationDecisionV1, ...] = ()
    attestation: StateAdjudicationAttestationV1 | None = None

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
        if len(self.subject_session_ids) != len(set(self.subject_session_ids)):
            raise ValueError("state adjudication subject sessions must be unique")
        if len(self.evidence_run_ids) != len(set(self.evidence_run_ids)):
            raise ValueError("state adjudication EvidenceRun ids must be unique")
        if self.reviewer_session_id in self.subject_session_ids:
            raise ValueError("state reviewer session cannot be an executing subject session")
        if self.attestation is not None:
            if self.attestation.reviewer_id != self.adjudicator_id:
                raise ValueError("state adjudication attestation reviewer mismatch")
            if self.attestation.reviewer_role != self.reviewer_role:
                raise ValueError("state adjudication attestation role mismatch")
            if self.attestation.reviewer_session_id != self.reviewer_session_id:
                raise ValueError("state adjudication attestation session mismatch")
            if self.attestation.payload_sha256 != _sha256_bytes(
                state_adjudication_signing_bytes(self)
            ):
                raise ValueError("state adjudication attestation payload hash mismatch")
        return self

    def attestation_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"attestation"},
        )


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
    assignment_version_ids: tuple[str, ...] = ()
    transition_version_ids: tuple[str, ...]
    replay_seed_path: str | None = None
    replay_seed_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    persistence_reopen_verified: bool


def state_adjudication_signing_bytes(seed: StateAdjudicationSeedV1) -> bytes:
    return json.dumps(
        seed.attestation_payload(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class StateAdjudicationSigner:
    """Private-key holder used by a reviewer session that did not execute the game."""

    def __init__(
        self,
        *,
        reviewer_id: str,
        reviewer_role: PlayerSoftSignalReviewerRole,
        reviewer_session_id: str,
        key_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        self.reviewer_id = reviewer_id
        self.reviewer_role = reviewer_role
        self.reviewer_session_id = reviewer_session_id
        self.key_id = key_id
        self._private_key = private_key

    @classmethod
    def generate(
        cls,
        *,
        reviewer_id: str,
        reviewer_role: PlayerSoftSignalReviewerRole,
        reviewer_session_id: str,
        key_id: str,
    ) -> "StateAdjudicationSigner":
        return cls(
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            reviewer_session_id=reviewer_session_id,
            key_id=key_id,
            private_key=Ed25519PrivateKey.generate(),
        )

    @classmethod
    def from_private_key_base64(
        cls,
        *,
        reviewer_id: str,
        reviewer_role: PlayerSoftSignalReviewerRole,
        reviewer_session_id: str,
        key_id: str,
        private_key_base64: str,
    ) -> "StateAdjudicationSigner":
        raw = base64.b64decode(private_key_base64, validate=True)
        if len(raw) != 32:
            raise ValueError("state reviewer private key must contain 32 raw bytes")
        return cls(
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            reviewer_session_id=reviewer_session_id,
            key_id=key_id,
            private_key=Ed25519PrivateKey.from_private_bytes(raw),
        )

    def public_identity(
        self,
        *,
        trust_scope: PlayerSoftSignalReviewTrustScope = "formal_external",
    ) -> PlayerSoftSignalReviewerPublicKeyV1:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return PlayerSoftSignalReviewerPublicKeyV1(
            reviewer_id=self.reviewer_id,
            reviewer_role=self.reviewer_role,
            key_id=self.key_id,
            public_key_base64=base64.b64encode(raw).decode("ascii"),
            trust_scope=trust_scope,
        )

    def sign(self, seed: StateAdjudicationSeedV1) -> StateAdjudicationSeedV1:
        if seed.attestation is not None:
            raise ValueError("state adjudication seed is already attested")
        if seed.adjudicator_id != self.reviewer_id:
            raise ValueError("state adjudication reviewer identity does not match signer")
        if seed.reviewer_role != self.reviewer_role:
            raise ValueError("state adjudication reviewer role does not match signer")
        if seed.reviewer_session_id != self.reviewer_session_id:
            raise ValueError("state adjudication reviewer session does not match signer")
        payload = state_adjudication_signing_bytes(seed)
        attestation = StateAdjudicationAttestationV1(
            reviewer_id=self.reviewer_id,
            reviewer_role=self.reviewer_role,
            reviewer_session_id=self.reviewer_session_id,
            key_id=self.key_id,
            payload_sha256=_sha256_bytes(payload),
            signature_base64=base64.b64encode(self._private_key.sign(payload)).decode("ascii"),
            issued_at=seed.reviewed_at,
        )
        return seed.model_copy(update={"attestation": attestation})


class StateAdjudicatorTrustStore:
    """State-review verifier backed by the facility's independent reviewer keys."""

    DEFAULT_RELATIVE_PATH = (
        Path("runtime") / "soft_signal_reviewer" / "trusted-reviewers.json"
    )

    def __init__(
        self,
        identities: Iterable[PlayerSoftSignalReviewerPublicKeyV1] = (),
    ) -> None:
        self._identities = {identity.key_id: identity for identity in identities}

    @classmethod
    def from_file(cls, path: Path) -> "StateAdjudicatorTrustStore":
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            raise ValueError("state adjudicator trust root must contain a JSON list")
        return cls(PlayerSoftSignalReviewerPublicKeyV1.model_validate(item) for item in values)

    @classmethod
    def default_file(cls, observatory_root: Path) -> Path:
        return observatory_root.resolve() / cls.DEFAULT_RELATIVE_PATH

    def verify(self, seed: StateAdjudicationSeedV1) -> None:
        attestation = seed.attestation
        if attestation is None:
            raise ValueError("state adjudication requires a trusted reviewer attestation")
        identity = self._identities.get(attestation.key_id)
        if identity is None or identity.status != "active":
            raise ValueError("state adjudicator key is not an active trust root")
        if identity.reviewer_id != seed.adjudicator_id:
            raise ValueError("state adjudicator identity is not bound to the trusted key")
        if identity.reviewer_role != seed.reviewer_role:
            raise ValueError("state adjudicator role is not bound to the trusted key")
        if identity.trust_scope != seed.trust_scope:
            raise ValueError("state adjudicator trust scope is not bound to the trusted key")
        payload = state_adjudication_signing_bytes(seed)
        if attestation.payload_sha256 != _sha256_bytes(payload):
            raise ValueError("state adjudication signed payload hash mismatch")
        try:
            signature = base64.b64decode(attestation.signature_base64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(identity.public_key_base64, validate=True)
            )
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError) as error:
            raise ValueError("state adjudicator signature is invalid") from error


def sign_state_adjudication_seed(
    unsigned_seed_path: Path,
    private_key_path: Path,
    output_path: Path,
) -> tuple[StateAdjudicationSeedV1, str]:
    seed = StateAdjudicationSeedV1.model_validate_json(unsigned_seed_path.read_bytes())
    if seed.attestation is not None:
        raise ValueError("state adjudication seed is already attested")
    key = json.loads(private_key_path.read_text(encoding="utf-8"))
    if not isinstance(key, dict):
        raise ValueError("state reviewer private key file must contain a JSON object")
    signer = StateAdjudicationSigner.from_private_key_base64(
        reviewer_id=str(key.get("reviewer_id", "")),
        reviewer_role=str(key.get("reviewer_role", "")),  # type: ignore[arg-type]
        reviewer_session_id=seed.reviewer_session_id,
        key_id=str(key.get("key_id", "")),
        private_key_base64=str(key.get("private_key_base64", "")),
    )
    signed = signer.sign(seed)
    content = _canonical_bytes(signed)
    _write_atomic(output_path, content)
    return signed, _sha256_bytes(content)


def _review_artifact(
    observatory: ObservatoryStore,
    artifact_id: str,
    *,
    cache: dict[str, StateReviewArtifactV1] | None = None,
    verify_content: bool = True,
) -> StateReviewArtifactV1:
    if cache is not None and artifact_id in cache:
        return cache[artifact_id]
    artifact = observatory.get_artifact(artifact_id)
    if artifact is None:
        raise ValueError(f"unknown review artifact: {artifact_id}")
    root = observatory.artifact_root.resolve()
    path = Path(artifact.path).resolve()
    if not _inside(path, root):
        raise ValueError(f"review artifact escapes canonical root: {artifact_id}")
    if not path.is_file():
        raise ValueError(f"review artifact file is missing: {artifact_id}")
    size_bytes = path.stat().st_size
    if verify_content:
        content = path.read_bytes()
        if _sha256_bytes(content) != artifact.sha256:
            raise ValueError(f"review artifact hash mismatch: {artifact_id}")
        size_bytes = len(content)
    reviewed = StateReviewArtifactV1(
        id=artifact.id,
        kind=artifact.kind,
        media_type=artifact.media_type,
        path=str(path),
        sha256=artifact.sha256,
        size_bytes=size_bytes,
    )
    if cache is not None:
        cache[artifact_id] = reviewed
    return reviewed


def _artifacts_for_ids(
    observatory: ObservatoryStore,
    artifact_ids: list[str],
    *,
    cache: dict[str, StateReviewArtifactV1] | None = None,
    verify_content: bool = True,
) -> tuple[StateReviewArtifactV1, ...]:
    return tuple(
        _review_artifact(
            observatory,
            artifact_id,
            cache=cache,
            verify_content=verify_content,
        )
        for artifact_id in dict.fromkeys(artifact_ids)
    )


def _state_review_candidate(
    player: AIPlayerStore,
    environment_id: str,
    state: SemanticStateV1,
    *,
    artifact_cache: dict[str, StateReviewArtifactV1] | None = None,
    verify_artifact_content: bool = True,
) -> StateReviewCandidateV1:
    assignments = tuple(
        player.list_state_assignments(
            environment_id,
            state_id=state.id,
            latest_only=True,
        )
    )
    if not assignments:
        raise ValueError(f"review state has no current assignments: {state.id}")
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
        raise ValueError(f"review state has a missing observation: {state.id}")
    references = [
        reference
        for item in (state, *assignments, *observations)
        for reference in item.evidence_refs
    ]
    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown environment: {environment_id}")
    player.resolve_evidence_references(references, environment_scope=environment)
    artifact_ids = [
        artifact_id
        for reference in references
        for artifact_id in reference.artifact_ids
    ]
    return StateReviewCandidateV1(
        state=state,
        state_sha256=_model_sha256(state),
        assignments=assignments,
        observations=observations,
        artifacts=_artifacts_for_ids(
            player.observatory_store,
            artifact_ids,
            cache=artifact_cache,
            verify_content=verify_artifact_content,
        ),
    )


def _transition_review_candidate(
    player: AIPlayerStore,
    environment_id: str,
    edge: TransitionEdgeV1,
    *,
    artifact_cache: dict[str, StateReviewArtifactV1] | None = None,
    verify_artifact_content: bool = True,
) -> TransitionReviewCandidateV1:
    source = player.get_semantic_state(environment_id, edge.from_state_id)
    destination = (
        player.get_semantic_state(environment_id, edge.to_state_id)
        if edge.to_state_id is not None
        else None
    )
    if source is None or (edge.to_state_id is not None and destination is None):
        raise ValueError(f"deferred edge endpoint is missing: {edge.id}")
    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown environment: {environment_id}")
    player.resolve_evidence_references(edge.evidence_refs, environment_scope=environment)
    artifact_ids = [
        artifact_id
        for reference in edge.evidence_refs
        for artifact_id in reference.artifact_ids
    ]
    return TransitionReviewCandidateV1(
        edge=edge,
        edge_sha256=_model_sha256(edge),
        from_state_title=source.title,
        from_state_status=source.status,
        to_state_title=destination.title if destination is not None else None,
        to_state_status=destination.status if destination is not None else None,
        artifacts=_artifacts_for_ids(
            player.observatory_store,
            artifact_ids,
            cache=artifact_cache,
            verify_content=verify_artifact_content,
        ),
    )


def _evidence_step_artifact_ids(step: EvidenceStep) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *step.artifact_ids,
                *step.intermediate_frame_ids,
                *(
                    [step.before_frame_id]
                    if step.before_frame_id is not None
                    else []
                ),
                *(
                    [step.before_ui_tree_id]
                    if step.before_ui_tree_id is not None
                    else []
                ),
                *(
                    [step.after_frame_id]
                    if step.after_frame_id is not None
                    else []
                ),
                *(
                    [step.after_ui_tree_id]
                    if step.after_ui_tree_id is not None
                    else []
                ),
                *(
                    [step.video_artifact_id]
                    if step.video_artifact_id is not None
                    else []
                ),
            ]
        )
    )


def _review_evidence_run(
    observatory: ObservatoryStore,
    run_id: str,
    *,
    artifact_cache: dict[str, StateReviewArtifactV1] | None = None,
    verify_artifact_content: bool = True,
) -> StateReviewEvidenceRunV1:
    run = observatory.get_evidence_run(run_id)
    if run is None:
        raise ValueError(f"state review EvidenceRun is missing: {run_id}")
    steps: list[StateReviewEvidenceStepV1] = []
    all_artifact_ids = list(run.artifact_ids)
    for step_id in run.step_ids:
        step = observatory.get_evidence_step(step_id)
        if step is None:
            raise ValueError(f"state review EvidenceStep is missing: {step_id}")
        if step.evidence_run_id != run.id:
            raise ValueError(f"state review EvidenceStep belongs to another run: {step_id}")
        artifact_ids = _evidence_step_artifact_ids(step)
        all_artifact_ids.extend(artifact_ids)
        steps.append(
            StateReviewEvidenceStepV1(
                step=step,
                step_sha256=_model_sha256(step),
                artifacts=_artifacts_for_ids(
                    observatory,
                    artifact_ids,
                    cache=artifact_cache,
                    verify_content=verify_artifact_content,
                ),
            )
        )
    return StateReviewEvidenceRunV1(
        run=run,
        run_sha256=_model_sha256(run),
        steps=tuple(steps),
        artifacts=_artifacts_for_ids(
            observatory,
            all_artifact_ids,
            cache=artifact_cache,
            verify_content=verify_artifact_content,
        ),
    )


def _packet_evidence_run_ids(
    states: Sequence[StateReviewCandidateV1],
    edges: Sequence[TransitionReviewCandidateV1],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            run_id
            for item in states
            for entity in (item.state, *item.assignments, *item.observations)
            for reference in entity.evidence_refs
            for run_id in reference.evidence_run_ids
        )
        | dict.fromkeys(
            run_id
            for item in edges
            for reference in item.edge.evidence_refs
            for run_id in reference.evidence_run_ids
        )
    )


def _subject_sessions_for_runs(
    runs: Sequence[StateReviewEvidenceRunV1],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(session_id)
            if (session_id := item.run.environment.get("ai_player_session_id"))
            else f"unbound-evidence-run:{item.run.id}"
            for item in runs
        )
    )


def build_state_review_packet(
    player: AIPlayerStore,
    environment_id: str,
    *,
    verify_artifact_content: bool = True,
) -> StateReviewPacketV1:
    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown environment: {environment_id}")
    selection = player.select_environment_lineage(environment_id)
    if selection.selected_environment_id != environment_id:
        raise ValueError("state review requires the current environment leaf")

    artifact_cache: dict[str, StateReviewArtifactV1] = {}
    states = [
        _state_review_candidate(
            player,
            environment_id,
            state,
            artifact_cache=artifact_cache,
            verify_artifact_content=verify_artifact_content,
        )
        for state in player.list_semantic_states(
            environment_id,
            statuses=("candidate",),
        )
    ]
    edges = [
        _transition_review_candidate(
            player,
            environment_id,
            edge,
            artifact_cache=artifact_cache,
            verify_artifact_content=verify_artifact_content,
        )
        for edge in player.list_transition_edges(environment_id, outcomes=("deferred",))
    ]

    decision_state_ids = {item.state.id for item in states}
    endpoint_state_ids = sorted(
        {
            state_id
            for item in edges
            for state_id in (item.edge.from_state_id, item.edge.to_state_id)
            if state_id is not None
        }
    )
    context_states = []
    for state_id in endpoint_state_ids:
        if state_id in decision_state_ids:
            continue
        state = player.get_semantic_state(environment_id, state_id)
        if state is None:
            raise ValueError(f"deferred edge endpoint is missing: {state_id}")
        context_states.append(
            _state_review_candidate(
                player,
                environment_id,
                state,
                artifact_cache=artifact_cache,
                verify_artifact_content=verify_artifact_content,
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
    evidence_run_ids = _packet_evidence_run_ids(
        [*states, *context_states],
        edges,
    )
    evidence_runs = tuple(
        _review_evidence_run(
            player.observatory_store,
            run_id,
            artifact_cache=artifact_cache,
            verify_artifact_content=verify_artifact_content,
        )
        for run_id in evidence_run_ids
    )
    return StateReviewPacketV1(
        packet_id=f"state-review.{digest[:32]}",
        environment_id=environment_id,
        evidence_run_ids=evidence_run_ids,
        subject_session_ids=_subject_sessions_for_runs(evidence_runs),
        states=tuple(states),
        edges=tuple(edges),
        context_states=tuple(context_states),
        evidence_runs=evidence_runs,
    )


def _edge_run_ids(item: TransitionReviewCandidateV1) -> set[str]:
    return _transition_run_ids(item.edge)


def _transition_run_ids(edge: TransitionEdgeV1) -> set[str]:
    return {
        run_id
        for reference in edge.evidence_refs
        for run_id in reference.evidence_run_ids
    }


def _edge_step_ids(item: TransitionReviewCandidateV1) -> set[str]:
    return _transition_step_ids(item.edge)


def _transition_step_ids(edge: TransitionEdgeV1) -> set[str]:
    return {
        step_id
        for reference in edge.evidence_refs
        for step_id in reference.evidence_step_ids
    }


def _edge_endpoint_ids(item: TransitionReviewCandidateV1) -> set[str]:
    return _transition_endpoint_ids(item.edge)


def _transition_endpoint_ids(edge: TransitionEdgeV1) -> set[str]:
    return {
        state_id
        for state_id in (edge.from_state_id, edge.to_state_id)
        if state_id is not None
    }


def _is_related_transition_counterexample(
    selected: Sequence[TransitionEdgeV1],
    candidate: TransitionEdgeV1,
) -> bool:
    candidate_steps = _transition_step_ids(candidate)
    candidate_endpoints = _transition_endpoint_ids(candidate)
    return any(
        bool(candidate_steps & _transition_step_ids(item))
        or candidate_endpoints == _transition_endpoint_ids(item)
        or (
            candidate.from_state_id == item.from_state_id
            and candidate.action.type == item.action.type
        )
        for item in selected
    )


def _is_related_edge_counterexample(
    selected: Sequence[TransitionReviewCandidateV1],
    candidate: TransitionReviewCandidateV1,
) -> bool:
    return _is_related_transition_counterexample(
        [item.edge for item in selected],
        candidate.edge,
    )


def _scoped_identity_payload(
    *,
    parent_packet_sha256: str,
    requested_state_ids: Sequence[str],
    requested_edge_ids: Sequence[str],
    merge_target_state_ids: Sequence[str],
    decision_state_ids: Sequence[str],
    decision_edge_ids: Sequence[str],
    context_state_ids: Sequence[str],
    context_edge_ids: Sequence[str],
) -> dict[str, object]:
    return {
        "parent_packet_sha256": parent_packet_sha256,
        "requested_state_ids": tuple(requested_state_ids),
        "requested_edge_ids": tuple(requested_edge_ids),
        "merge_target_state_ids": tuple(merge_target_state_ids),
        "decision_state_ids": list(decision_state_ids),
        "decision_edge_ids": list(decision_edge_ids),
        "context_state_ids": list(context_state_ids),
        "context_edge_ids": list(context_edge_ids),
    }


def _require_declared_scoped_packet_identity(packet: StateReviewPacketV1) -> None:
    scope = packet.scope
    if scope is None:
        return
    identity = _scoped_identity_payload(
        parent_packet_sha256=scope.parent_packet_sha256,
        requested_state_ids=scope.requested_state_ids,
        requested_edge_ids=scope.requested_edge_ids,
        merge_target_state_ids=scope.merge_target_state_ids,
        decision_state_ids=scope.decision_state_ids,
        decision_edge_ids=scope.decision_edge_ids,
        context_state_ids=scope.context_state_ids,
        context_edge_ids=scope.context_edge_ids,
    )
    digest = _sha256_bytes(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    valid_packet_ids = {f"state-review.scoped.{digest[:32]}"}
    if not scope.merge_target_state_ids:
        legacy_identity = dict(identity)
        legacy_identity.pop("merge_target_state_ids", None)
        legacy_digest = _sha256_bytes(
            json.dumps(
                legacy_identity,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
        valid_packet_ids.add(f"state-review.scoped.{legacy_digest[:32]}")
    if packet.packet_id not in valid_packet_ids:
        raise ValueError("scoped state review packet identity is invalid")


def _scoped_comparison_payload(packet: StateReviewPacketV1) -> dict[str, object]:
    """Return decision-relevant scope content without the broad parent identity."""

    if packet.scope is None:
        raise ValueError("state review packet is not scoped")
    payload = packet.model_dump(mode="json", by_alias=True)
    payload.pop("packet_id", None)
    scope = payload.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("scoped state review packet has no scope payload")
    scope.pop("parent_packet_id", None)
    scope.pop("parent_packet_sha256", None)
    return payload


def derive_scoped_state_review_packet(
    parent: StateReviewPacketV1,
    *,
    state_ids: Sequence[str] = (),
    edge_ids: Sequence[str] = (),
    merge_targets: Sequence[StateReviewCandidateV1] = (),
) -> StateReviewPacketV1:
    """Derive a deterministic, provenance-locked decision scope from a full packet."""

    if parent.scope is not None:
        raise ValueError("a scoped packet cannot be used as a review parent")
    requested_state_ids = tuple(state_ids)
    requested_edge_ids = tuple(edge_ids)
    merge_target_ids = tuple(item.state.id for item in merge_targets)
    if len(requested_state_ids) != len(set(requested_state_ids)):
        raise ValueError("requested state ids must be unique")
    if len(requested_edge_ids) != len(set(requested_edge_ids)):
        raise ValueError("requested edge ids must be unique")
    if len(merge_target_ids) != len(set(merge_target_ids)):
        raise ValueError("accepted merge target ids must be unique")
    if not requested_state_ids and not requested_edge_ids:
        raise ValueError("scoped state review requires an explicit state or edge")
    for item in merge_targets:
        if item.state.environment_id != parent.environment_id:
            raise ValueError(f"accepted merge target crosses environment: {item.state.id}")
        if item.state.status != "accepted":
            raise ValueError(f"merge target is not accepted: {item.state.id}")

    parent_states = {item.state.id: item for item in parent.states}
    parent_context_states = {item.state.id: item for item in parent.context_states}
    parent_edges = {item.edge.id: item for item in parent.edges}
    unknown_states = [item for item in requested_state_ids if item not in parent_states]
    if unknown_states:
        raise ValueError(
            "requested state is not a current candidate: " + ", ".join(unknown_states)
        )
    unknown_edges = [item for item in requested_edge_ids if item not in parent_edges]
    if unknown_edges:
        raise ValueError(
            "requested edge is not currently deferred: " + ", ".join(unknown_edges)
        )

    # A live replay must retain the complete EvidenceRun. Expand decision edges to all
    # currently deferred edges backed by the same run, so replay cannot silently
    # promote an omitted sibling transition.
    decision_edge_id_set = set(requested_edge_ids)
    while True:
        selected_run_ids = {
            run_id
            for edge_id in decision_edge_id_set
            for run_id in _edge_run_ids(parent_edges[edge_id])
        }
        expanded = {
            item.edge.id
            for item in parent.edges
            if _edge_run_ids(item) & selected_run_ids
        }
        if expanded.issubset(decision_edge_id_set):
            break
        decision_edge_id_set.update(expanded)
    decision_edges = tuple(
        item for item in parent.edges if item.edge.id in decision_edge_id_set
    )

    endpoint_state_id_set = {
        state_id
        for item in decision_edges
        for state_id in _edge_endpoint_ids(item)
    }
    decision_state_id_set = {
        *requested_state_ids,
        *(state_id for state_id in endpoint_state_id_set if state_id in parent_states),
    }
    decision_states = tuple(
        item for item in parent.states if item.state.id in decision_state_id_set
    )

    context_edges = tuple(
        item
        for item in parent.edges
        if item.edge.id not in decision_edge_id_set
        and _is_related_edge_counterexample(decision_edges, item)
    )
    context_state_id_set = {
        state_id
        for item in (*decision_edges, *context_edges)
        for state_id in _edge_endpoint_ids(item)
        if state_id not in decision_state_id_set
    }
    context_state_id_set.update(merge_target_ids)
    merge_targets_by_id = {item.state.id: item for item in merge_targets}
    all_parent_states = {
        **parent_states,
        **parent_context_states,
        **merge_targets_by_id,
    }
    missing_endpoints = sorted(context_state_id_set - set(all_parent_states))
    if missing_endpoints:
        raise ValueError(
            "scoped edge endpoint is absent from its parent packet: "
            + ", ".join(missing_endpoints)
        )
    context_state_items: list[StateReviewCandidateV1] = []
    seen_context_state_ids: set[str] = set()
    for item in (*parent.states, *parent.context_states, *merge_targets):
        if (
            item.state.id in context_state_id_set
            and item.state.id not in seen_context_state_ids
        ):
            context_state_items.append(item)
            seen_context_state_ids.add(item.state.id)
    context_states = tuple(context_state_items)

    evidence_run_ids = _packet_evidence_run_ids(
        [*decision_states, *context_states],
        [*decision_edges, *context_edges],
    )
    parent_runs = {item.run.id: item for item in parent.evidence_runs}
    missing_runs = sorted(set(evidence_run_ids) - set(parent_runs))
    if missing_runs:
        raise ValueError(
            "scoped review evidence is absent from its parent packet: "
            + ", ".join(missing_runs)
        )
    evidence_runs = tuple(parent_runs[run_id] for run_id in evidence_run_ids)
    parent_bytes = _canonical_bytes(parent)
    parent_sha256 = _sha256_bytes(parent_bytes)
    scope_identity = _scoped_identity_payload(
        parent_packet_sha256=parent_sha256,
        requested_state_ids=requested_state_ids,
        requested_edge_ids=requested_edge_ids,
        merge_target_state_ids=merge_target_ids,
        decision_state_ids=[item.state.id for item in decision_states],
        decision_edge_ids=[item.edge.id for item in decision_edges],
        context_state_ids=[item.state.id for item in context_states],
        context_edge_ids=[item.edge.id for item in context_edges],
    )
    digest = _sha256_bytes(
        json.dumps(scope_identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    scope = StateReviewScopeV1(
        parent_packet_id=parent.packet_id,
        parent_packet_sha256=parent_sha256,
        requested_state_ids=requested_state_ids,
        requested_edge_ids=requested_edge_ids,
        decision_state_ids=tuple(item.state.id for item in decision_states),
        decision_edge_ids=tuple(item.edge.id for item in decision_edges),
        endpoint_state_ids=tuple(sorted(endpoint_state_id_set)),
        merge_target_state_ids=merge_target_ids,
        context_state_ids=tuple(item.state.id for item in context_states),
        context_edge_ids=tuple(item.edge.id for item in context_edges),
    )
    return StateReviewPacketV1(
        packet_id=f"state-review.scoped.{digest[:32]}",
        environment_id=parent.environment_id,
        evidence_run_ids=evidence_run_ids,
        subject_session_ids=_subject_sessions_for_runs(evidence_runs),
        states=decision_states,
        edges=decision_edges,
        context_states=context_states,
        context_edges=context_edges,
        evidence_runs=evidence_runs,
        scope=scope,
    )


def build_scoped_state_review_packet(
    player: AIPlayerStore,
    environment_id: str,
    *,
    state_ids: Sequence[str] = (),
    edge_ids: Sequence[str] = (),
    merge_target_ids: Sequence[str] = (),
) -> StateReviewPacketV1:
    """Build a closed review scope without materialising the unrelated corpus.

    The parent commitment covers every current candidate state and deferred edge,
    while full observations, artifacts and EvidenceRuns are loaded only for the
    decision scope and its semantic counterexamples.  This preserves same-run and
    related-edge anti-omission checks without an N+1 walk over unrelated history.
    """

    requested_state_ids = tuple(state_ids)
    requested_edge_ids = tuple(edge_ids)
    requested_merge_target_ids = tuple(merge_target_ids)
    if len(requested_state_ids) != len(set(requested_state_ids)):
        raise ValueError("requested state ids must be unique")
    if len(requested_edge_ids) != len(set(requested_edge_ids)):
        raise ValueError("requested edge ids must be unique")
    if len(requested_merge_target_ids) != len(set(requested_merge_target_ids)):
        raise ValueError("accepted merge target ids must be unique")
    if not requested_state_ids and not requested_edge_ids:
        raise ValueError("scoped state review requires an explicit state or edge")

    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown environment: {environment_id}")
    selection = player.select_environment_lineage(environment_id)
    if selection.selected_environment_id != environment_id:
        raise ValueError("state review requires the current environment leaf")

    candidate_models = player.list_semantic_states(
        environment_id,
        statuses=("candidate",),
    )
    candidate_by_id = {item.id: item for item in candidate_models}
    requested_accepted_models: list[SemanticStateV1] = []
    for state_id in requested_state_ids:
        if state_id in candidate_by_id:
            continue
        state = player.get_semantic_state(environment_id, state_id)
        if state is not None and state.status == "accepted":
            requested_accepted_models.append(state)
    reviewable_models = [*candidate_models, *requested_accepted_models]
    reviewable_by_id = {item.id: item for item in reviewable_models}
    deferred_models = player.list_transition_edges(
        environment_id,
        outcomes=("deferred",),
    )
    deferred_by_id = {item.id: item for item in deferred_models}
    unknown_states = [item for item in requested_state_ids if item not in reviewable_by_id]
    if unknown_states:
        raise ValueError(
            "requested state is not a current candidate or accepted state: "
            + ", ".join(unknown_states)
        )
    unknown_edges = [item for item in requested_edge_ids if item not in deferred_by_id]
    if unknown_edges:
        raise ValueError(
            "requested edge is not currently deferred: " + ", ".join(unknown_edges)
        )

    decision_edge_id_set = set(requested_edge_ids)
    while True:
        selected_run_ids = {
            run_id
            for edge_id in decision_edge_id_set
            for run_id in _transition_run_ids(deferred_by_id[edge_id])
        }
        expanded = {
            item.id
            for item in deferred_models
            if _transition_run_ids(item) & selected_run_ids
        }
        if expanded.issubset(decision_edge_id_set):
            break
        decision_edge_id_set.update(expanded)
    decision_edge_models = tuple(
        item for item in deferred_models if item.id in decision_edge_id_set
    )
    endpoint_state_id_set = {
        state_id
        for item in decision_edge_models
        for state_id in _transition_endpoint_ids(item)
    }
    decision_state_id_set = {
        *requested_state_ids,
        *(state_id for state_id in endpoint_state_id_set if state_id in candidate_by_id),
    }
    decision_state_models = tuple(
        item for item in reviewable_models if item.id in decision_state_id_set
    )
    context_edge_models = tuple(
        item
        for item in deferred_models
        if item.id not in decision_edge_id_set
        and _is_related_transition_counterexample(decision_edge_models, item)
    )
    context_state_id_set = {
        state_id
        for item in (*decision_edge_models, *context_edge_models)
        for state_id in _transition_endpoint_ids(item)
        if state_id not in decision_state_id_set
    }
    context_state_id_set.update(requested_merge_target_ids)

    context_state_models: list[SemanticStateV1] = []
    seen_context_state_ids: set[str] = set()
    for state_id in sorted(context_state_id_set):
        state = player.get_semantic_state(environment_id, state_id)
        if state is None:
            if state_id in requested_merge_target_ids:
                raise ValueError(f"merge target is missing: {state_id}")
            raise ValueError(f"scoped edge endpoint is absent: {state_id}")
        if state_id in requested_merge_target_ids and state.status != "accepted":
            raise ValueError(f"merge target is not accepted: {state_id}")
        if state_id not in seen_context_state_ids:
            context_state_models.append(state)
            seen_context_state_ids.add(state_id)

    artifact_cache: dict[str, StateReviewArtifactV1] = {}
    decision_states = tuple(
        _state_review_candidate(
            player,
            environment_id,
            state,
            artifact_cache=artifact_cache,
        )
        for state in decision_state_models
    )
    decision_edges = tuple(
        _transition_review_candidate(
            player,
            environment_id,
            edge,
            artifact_cache=artifact_cache,
        )
        for edge in decision_edge_models
    )
    context_states = tuple(
        _state_review_candidate(
            player,
            environment_id,
            state,
            artifact_cache=artifact_cache,
        )
        for state in context_state_models
    )
    context_edges = tuple(
        _transition_review_candidate(
            player,
            environment_id,
            edge,
            artifact_cache=artifact_cache,
        )
        for edge in context_edge_models
    )
    evidence_run_ids = _packet_evidence_run_ids(
        [*decision_states, *context_states],
        [*decision_edges, *context_edges],
    )
    evidence_runs = tuple(
        _review_evidence_run(
            player.observatory_store,
            run_id,
            artifact_cache=artifact_cache,
        )
        for run_id in evidence_run_ids
    )

    parent_manifest = {
        "environment_id": environment_id,
        "states": [(item.id, _model_sha256(item)) for item in reviewable_models],
        "edges": [(item.id, _model_sha256(item)) for item in deferred_models],
    }
    parent_bytes = json.dumps(
        parent_manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    parent_sha256 = _sha256_bytes(parent_bytes)
    parent_packet_id = f"state-review.index.{parent_sha256[:32]}"
    scope_identity = _scoped_identity_payload(
        parent_packet_sha256=parent_sha256,
        requested_state_ids=requested_state_ids,
        requested_edge_ids=requested_edge_ids,
        merge_target_state_ids=requested_merge_target_ids,
        decision_state_ids=[item.state.id for item in decision_states],
        decision_edge_ids=[item.edge.id for item in decision_edges],
        context_state_ids=[item.state.id for item in context_states],
        context_edge_ids=[item.edge.id for item in context_edges],
    )
    digest = _sha256_bytes(
        json.dumps(scope_identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    scope = StateReviewScopeV1(
        parent_packet_id=parent_packet_id,
        parent_packet_sha256=parent_sha256,
        requested_state_ids=requested_state_ids,
        requested_edge_ids=requested_edge_ids,
        decision_state_ids=tuple(item.state.id for item in decision_states),
        decision_edge_ids=tuple(item.edge.id for item in decision_edges),
        endpoint_state_ids=tuple(sorted(endpoint_state_id_set)),
        merge_target_state_ids=requested_merge_target_ids,
        context_state_ids=tuple(item.state.id for item in context_states),
        context_edge_ids=tuple(item.edge.id for item in context_edges),
    )
    return StateReviewPacketV1(
        packet_id=f"state-review.scoped.{digest[:32]}",
        environment_id=environment_id,
        evidence_run_ids=evidence_run_ids,
        subject_session_ids=_subject_sessions_for_runs(evidence_runs),
        states=decision_states,
        edges=decision_edges,
        context_states=context_states,
        context_edges=context_edges,
        evidence_runs=evidence_runs,
        scope=scope,
    )


def export_state_review_packet(
    player: AIPlayerStore,
    environment_id: str,
    output_path: Path,
    *,
    state_ids: Sequence[str] = (),
    edge_ids: Sequence[str] = (),
    merge_target_ids: Sequence[str] = (),
) -> tuple[StateReviewPacketV1, str]:
    scoped = bool(state_ids or edge_ids or merge_target_ids)
    if scoped:
        packet = build_scoped_state_review_packet(
            player,
            environment_id,
            state_ids=state_ids,
            edge_ids=edge_ids,
            merge_target_ids=merge_target_ids,
        )
    else:
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
                *(
                    [f"merged-into:{decision.merge_into_state_id}"]
                    if decision.merge_into_state_id is not None
                    else []
                ),
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
            "surface_profile": decision.surface_profile or current.surface_profile,
            "surface_anchors": (
                list(decision.surface_anchors)
                if decision.surface_anchors is not None
                else current.surface_anchors
            ),
            "status": decision.disposition,
            "supersedes_id": current.id,
            "created_at": seed.reviewed_at,
        }
    )
    return SemanticStateV1.model_validate(payload)


def _replay_reviewed_transitions(
    *,
    store_root: Path,
    packet: StateReviewPacketV1,
    seed: StateAdjudicationSeedV1,
    result_path: Path,
) -> tuple[list[TransitionEdgeV1], Path | None, str | None]:
    from .live_evidence_state_ingest import (
        EvidenceRunSelectionV1,
        LiveEvidenceStateIngestSeedV1,
        ingest_live_evidence_state_seed,
    )

    reviewed_step_ids = {
        step_id
        for decision in seed.transition_decisions
        for step_id in decision.reviewed_evidence_step_ids
    }
    replay_run_ids = tuple(
        dict.fromkeys(
            run_id
            for item in packet.edges
            for reference in item.edge.evidence_refs
            for run_id in reference.evidence_run_ids
        )
    )
    if not replay_run_ids:
        return [], None, None
    observatory = ObservatoryStore(store_root)
    embedded_runs = {item.run.id: item for item in packet.evidence_runs}
    selections: list[EvidenceRunSelectionV1] = []
    for run_id in replay_run_ids:
        run = observatory.get_evidence_run(run_id)
        if run is None:
            raise ValueError(f"reviewed transition EvidenceRun is missing: {run_id}")
        embedded = embedded_runs.get(run_id)
        if embedded is None:
            if packet.scope is not None:
                raise ValueError(f"review packet omits complete EvidenceRun: {run_id}")
        elif embedded != _review_evidence_run(observatory, run_id):
            raise ValueError(f"review packet EvidenceRun changed after export: {run_id}")
        if packet.scope is None and not set(run.step_ids).issubset(reviewed_step_ids):
            raise ValueError(
                f"review packet does not cover the complete EvidenceRun: {run_id}"
            )
        selections.append(
            EvidenceRunSelectionV1(
                evidence_run_id=run.id,
                evidence_step_ids=tuple(run.step_ids),
            )
        )
    replay_seed = LiveEvidenceStateIngestSeedV1(
        seed_id=f"live-state.replay.{seed.seed_id}",
        environment_id=seed.environment_id,
        runs=tuple(selections),
    )
    replay_path = result_path.with_name(f"{seed.seed_id}.live-replay.v1.json")
    replay_bytes = _canonical_bytes(replay_seed)
    replay_sha256 = _sha256_bytes(replay_bytes)
    _write_atomic(replay_path, replay_bytes)
    replay_result = ingest_live_evidence_state_seed(
        store_root,
        replay_path,
        expected_store_root=store_root,
        expected_seed_sha256=replay_sha256,
    )
    player = AIPlayerStore(ObservatoryStore(store_root))
    edges = [
        edge
        for edge_id in replay_result.transition_edge_ids
        if (edge := player.get_transition_edge(seed.environment_id, edge_id)) is not None
    ]
    if len(edges) != len(replay_result.transition_edge_ids):
        raise RuntimeError("reviewed transition replay lost a canonical edge")
    return edges, replay_path, replay_sha256


def apply_state_adjudication_seed(
    store_root: Path,
    packet_path: Path,
    seed_path: Path,
    result_path: Path,
    *,
    expected_store_root: Path,
    expected_seed_sha256: str,
    trust_store: StateAdjudicatorTrustStore,
) -> StateAdjudicationResultV1:
    resolved_root = store_root.resolve()
    if resolved_root != expected_store_root.resolve():
        raise ValueError("AI-player store root mismatch")
    seed_bytes = seed_path.read_bytes()
    seed_sha256 = _sha256_bytes(seed_bytes)
    if seed_sha256 != expected_seed_sha256.lower():
        raise ValueError("state adjudication seed SHA-256 mismatch")
    seed = StateAdjudicationSeedV1.model_validate_json(seed_bytes)
    trust_store.verify(seed)
    packet_bytes = packet_path.read_bytes()
    packet_sha256 = _sha256_bytes(packet_bytes)
    if packet_sha256 != seed.packet_sha256:
        raise ValueError("state review packet SHA-256 mismatch")
    packet = StateReviewPacketV1.model_validate_json(packet_bytes)
    if packet.environment_id != seed.environment_id:
        raise ValueError("state review packet environment mismatch")
    observatory = ObservatoryStore(resolved_root)
    player = AIPlayerStore(observatory)
    if packet.scope is not None:
        _require_declared_scoped_packet_identity(packet)
        existing_scope_ledger = player.get_state_adjudication(
            seed.environment_id,
            seed.seed_id,
        )
        if existing_scope_ledger is not None:
            if (
                existing_scope_ledger["packet_sha256"] != packet_sha256
                or existing_scope_ledger["seed_sha256"] != seed_sha256
            ):
                raise ValueError(
                    f"state adjudication is immutable: {seed.environment_id}/{seed.seed_id}"
                )
        else:
            expected_scoped = build_scoped_state_review_packet(
                player,
                packet.environment_id,
                state_ids=packet.scope.requested_state_ids,
                edge_ids=packet.scope.requested_edge_ids,
                merge_target_ids=packet.scope.merge_target_state_ids,
            )
            if _scoped_comparison_payload(expected_scoped) != _scoped_comparison_payload(
                packet
            ):
                raise ValueError("scoped state review contents changed after export")
    if set(packet.subject_session_ids) != set(seed.subject_session_ids):
        raise ValueError("state adjudication subject sessions do not match review packet")
    if set(packet.evidence_run_ids) != set(seed.evidence_run_ids):
        raise ValueError("state adjudication EvidenceRun ids do not match review packet")
    if seed.trust_scope == "formal_external" and any(
        item.startswith("unbound-evidence-run:") for item in seed.subject_session_ids
    ):
        raise ValueError("formal state adjudication requires bound executing sessions")
    if {item.state_id for item in seed.state_decisions} != {
        item.state.id for item in packet.states
    }:
        raise ValueError("state adjudication must decide every packet state")
    if {item.edge_id for item in seed.transition_decisions} != {
        item.edge.id for item in packet.edges
    }:
        raise ValueError("state adjudication must review every packet edge")

    environment = player.get_environment(seed.environment_id)
    if environment is None:
        raise KeyError(f"unknown environment: {seed.environment_id}")
    existing_ledger = player.get_state_adjudication(seed.environment_id, seed.seed_id)
    state_revisions: list[SemanticStateV1] = []
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
        if existing_ledger is None and current != desired and (
            current.version != decision.expected_version
            or _model_sha256(current) != source.state_sha256
        ):
            raise ValueError(f"state decision target changed after review: {decision.state_id}")
        current_assignments = player.list_state_assignments(
            seed.environment_id,
            state_id=current.id,
            latest_only=True,
        )
        if existing_ledger is None and {
            item.observation_id for item in current_assignments
        } != set(packet_observation_ids):
            raise ValueError(f"state assignments changed after review: {decision.state_id}")
        state_revisions.append(desired)

    decisions_by_id = {item.state_id: item for item in seed.state_decisions}
    packet_states_by_id = {item.state.id: item for item in packet.states}
    packet_context_states_by_id = {
        item.state.id: item for item in packet.context_states
    }
    allowed_accepted_merge_targets = (
        set(packet.scope.merge_target_state_ids) if packet.scope is not None else set()
    )
    revisions_by_id = {item.id: item for item in state_revisions}
    assignment_revisions: list[StateAssignmentV1] = []
    merges_by_target: dict[str, list[str]] = {}
    for decision in seed.state_decisions:
        if decision.merge_into_state_id is not None:
            merges_by_target.setdefault(decision.merge_into_state_id, []).append(
                decision.state_id
            )
    for target_id, source_ids in merges_by_target.items():
        target_decision = decisions_by_id.get(target_id)
        target_is_candidate_decision = (
            target_decision is not None and target_decision.disposition == "accepted"
        )
        target_is_existing_accepted = target_id in allowed_accepted_merge_targets
        if not target_is_candidate_decision and not target_is_existing_accepted:
            raise ValueError(
                "merge target must be accepted in the same review or declared as a "
                f"scoped accepted merge target: {target_id}"
            )
        target_packet = (
            packet_states_by_id[target_id]
            if target_is_candidate_decision
            else packet_context_states_by_id[target_id]
        )
        if target_is_existing_accepted:
            current_target = player.get_semantic_state(seed.environment_id, target_id)
            if current_target is None:
                raise ValueError(f"merge target is missing: {target_id}")
            if current_target.status != "accepted":
                raise ValueError(f"merge target is not accepted: {target_id}")
            if (
                current_target.version != target_packet.state.version
                or _model_sha256(current_target) != target_packet.state_sha256
            ):
                raise ValueError(f"accepted merge target changed after review: {target_id}")
        merge_packets = [target_packet, *(packet_states_by_id[item] for item in source_ids)]
        merge_observations = [
            observation for item in merge_packets for observation in item.observations
        ]
        for index, left in enumerate(merge_observations):
            for right in merge_observations[index + 1 :]:
                conflicts = critical_feature_conflicts(left.features, right.features)
                if conflicts:
                    raise ValueError(
                        "cannot merge states with conflicting critical features: "
                        + ", ".join(conflicts)
                    )
        source_states = [packet_states_by_id[item].state for item in source_ids]
        if target_is_candidate_decision:
            target = revisions_by_id[target_id]
            target_payload = target.model_dump()
            target_payload.update(
                {
                    "observation_feature_hashes": list(
                        dict.fromkeys(
                            [
                                *target.observation_feature_hashes,
                                *(
                                    feature_hash
                                    for state in source_states
                                    for feature_hash in state.observation_feature_hashes
                                ),
                            ]
                        )
                    ),
                    "aliases": list(
                        dict.fromkeys(
                            [
                                *target.aliases,
                                *(state.title for state in source_states),
                                *source_ids,
                            ]
                        )
                    ),
                    "tags": list(
                        dict.fromkeys(
                            [
                                *target.tags,
                                *(f"merged-from:{item}" for item in source_ids),
                            ]
                        )
                    ),
                    "evidence_refs": _unique_evidence_refs(
                        [
                            *target.evidence_refs,
                            *(
                                reference
                                for state in source_states
                                for reference in state.evidence_refs
                            ),
                        ]
                    ),
                }
            )
            revisions_by_id[target_id] = SemanticStateV1.model_validate(target_payload)
        for source_id in source_ids:
            for assignment in packet_states_by_id[source_id].assignments:
                assignment_revisions.append(
                    StateAssignmentV1(
                        id=f"assignment.{assignment.observation_id}.v{assignment.version + 1}",
                        version=assignment.version + 1,
                        environment_id=seed.environment_id,
                        observation_id=assignment.observation_id,
                        state_id=target_id,
                        method="adjudicated_merge",
                        confidence=1.0,
                        reasons=[f"签名裁决 {seed.seed_id} 将同义状态归并到 {target_id}"],
                        status="active",
                        supersedes_id=assignment.id,
                        evidence_refs=_unique_evidence_refs(
                            [
                                *assignment.evidence_refs,
                                *packet_states_by_id[source_id].state.evidence_refs,
                                *target_packet.state.evidence_refs,
                            ]
                        ),
                        created_at=seed.reviewed_at,
                    )
                )
    state_revisions = [revisions_by_id[item.state_id] for item in seed.state_decisions]
    merge_targets = {
        source_id: target_id
        for target_id, source_ids in merges_by_target.items()
        for source_id in source_ids
    }

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
        reviewed_version = player.get_transition_edge(
            seed.environment_id,
            decision.edge_id,
            version=decision.expected_version,
        )
        current = player.get_transition_edge(seed.environment_id, decision.edge_id)
        if reviewed_version is None or current is None:
            raise ValueError(f"edge decision target is missing: {decision.edge_id}")
        if (
            _model_sha256(reviewed_version) != source.edge_sha256
            or reviewed_version != source.edge
        ):
            raise ValueError(f"edge decision target changed after review: {decision.edge_id}")
        if existing_ledger is None and current != reviewed_version:
            raise ValueError(f"edge advanced before review commit: {decision.edge_id}")
        reviewed_from_state_id = merge_targets.get(
            reviewed_version.from_state_id,
            reviewed_version.from_state_id,
        )
        reviewed_to_state_id = (
            merge_targets.get(reviewed_version.to_state_id, reviewed_version.to_state_id)
            if reviewed_version.to_state_id is not None
            else None
        )
        same_endpoint = reviewed_from_state_id == reviewed_to_state_id
        if decision.semantic_observation == "distinct_state" and same_endpoint:
            raise ValueError(f"distinct-state review has identical endpoints: {decision.edge_id}")
        if decision.semantic_observation.startswith("same_state_") and not same_endpoint:
            raise ValueError(f"same-state review has distinct endpoints: {decision.edge_id}")

    ledger_body_json = seed.model_dump_json(by_alias=True)
    commit_payload = {
        "seed_id": seed.seed_id,
        "seed_sha256": seed_sha256,
        "packet_sha256": packet_sha256,
        "state_versions": [
            {
                "id": item.id,
                "version": item.version,
                "sha256": _model_sha256(item),
            }
            for item in state_revisions
        ],
        "assignment_versions": [
            {
                "id": item.id,
                "version": item.version,
                "observation_id": item.observation_id,
                "state_id": item.state_id,
            }
            for item in assignment_revisions
        ],
    }
    result_sha256 = _sha256_bytes(
        json.dumps(commit_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    state_results = list(
        player.apply_state_adjudication(
            environment_id=seed.environment_id,
            adjudication_id=seed.seed_id,
            packet_sha256=packet_sha256,
            seed_sha256=seed_sha256,
            reviewer_id=seed.adjudicator_id,
            reviewer_session_id=seed.reviewer_session_id,
            subject_session_ids=seed.subject_session_ids,
            state_revisions=state_revisions,
            assignment_revisions=assignment_revisions,
            transition_evidence_refs=tuple(
                reference
                for item in packet.edges
                for reference in item.edge.evidence_refs
            ),
            adjudication_body_json=ledger_body_json,
            result_sha256=result_sha256,
            created_at=seed.reviewed_at,
        )
    )
    edge_results, replay_seed_path, replay_seed_sha256 = _replay_reviewed_transitions(
        store_root=resolved_root,
        packet=packet,
        seed=seed,
        result_path=result_path,
    )

    reopened = AIPlayerStore(ObservatoryStore(resolved_root))
    persistence_verified = all(
        reopened.get_semantic_state(seed.environment_id, item.id) == item
        for item in state_results
    ) and all(
        reopened.get_transition_edge(seed.environment_id, item.id) == item
        for item in edge_results
    ) and all(
        reopened.get_current_state_assignment(
            seed.environment_id,
            item.observation_id,
        )
        == item
        for item in assignment_revisions
    ) and reopened.get_state_adjudication(seed.environment_id, seed.seed_id) is not None
    result = StateAdjudicationResultV1(
        seed_id=seed.seed_id,
        seed_sha256=seed_sha256,
        packet_sha256=packet_sha256,
        environment_id=seed.environment_id,
        adjudicator_id=seed.adjudicator_id,
        state_version_ids=tuple(f"{item.id}@{item.version}" for item in state_results),
        assignment_version_ids=tuple(
            f"{item.observation_id}@{item.version}" for item in assignment_revisions
        ),
        transition_version_ids=tuple(f"{item.id}@{item.version}" for item in edge_results),
        replay_seed_path=(str(replay_seed_path) if replay_seed_path is not None else None),
        replay_seed_sha256=replay_seed_sha256,
        persistence_reopen_verified=persistence_verified,
    )
    _write_atomic(result_path, _canonical_bytes(result))
    if not persistence_verified:
        raise RuntimeError("state adjudication persistence verification failed")
    return result
