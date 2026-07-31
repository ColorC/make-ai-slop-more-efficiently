"""Deterministically turn terminal game evidence into the current AI-player state graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import cv2
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..evidence import (
    perceptual_frame_distance,
    regional_structural_frame_distance,
    structural_frame_distance,
)
from ..models import (
    LIFECYCLE_ACTION_TYPES,
    ArtifactRef,
    EvidenceRun,
    EvidenceStep,
    SourcePixelRect,
)
from ..store import ObservatoryStore
from .contracts import (
    EvidenceReferenceV1,
    SemanticSurfaceProfileV1,
    StateObservationFeaturesV1,
    StateObservationV1,
)
from .state_recognition import build_state_observation
from .store import AIPlayerStore, StateTransitionIntent
from .skill_attestation import skill_runtime_signer_and_trust_store
from .semantic_surface_profiles import resolve_semantic_surface_profile


SEED_SCHEMA = "game-observatory.ai-player.live-evidence-state-ingest-seed.v1"
RESULT_SCHEMA = "game-observatory.ai-player.live-evidence-state-ingest-result.v1"
_NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:[.,]\d+)*(?:%|万|亿)?")
_TIMER_PATTERN = re.compile(
    r"(?:\d{1,3}:){1,2}\d{1,2}|倒计时|剩余|计时|小时|分钟|秒(?:钟)?",
    re.IGNORECASE,
)
_RESOURCE_WORDS = (
    "木材",
    "铁矿",
    "石料",
    "粮食",
    "金币",
    "银币",
    "铜币",
    "体力",
    "兵力",
    "预备兵",
    "资源",
    "库存",
    "数量",
    "繁荣度",
    "战力",
)


class EvidenceRunSelectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_run_id: str = Field(min_length=1)
    evidence_step_ids: tuple[str, ...] = Field(min_length=1)
    verified_skill_run_id: str | None = Field(default=None, min_length=1)

    @field_validator("evidence_step_ids")
    @classmethod
    def require_unique_steps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence step ids must be unique")
        return value


class LiveEvidenceStateIngestSeedV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.live-evidence-state-ingest-seed.v1"
    ] = Field(default=SEED_SCHEMA, alias="schema")
    seed_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    runs: tuple[EvidenceRunSelectionV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_runs(self) -> "LiveEvidenceStateIngestSeedV1":
        run_ids = [item.evidence_run_id for item in self.runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("evidence run ids must be unique")
        return self


class LiveEvidenceStateIngestResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.live-evidence-state-ingest-result.v1"
    ] = Field(default=RESULT_SCHEMA, alias="schema")
    seed_id: str
    seed_sha256: str = Field(min_length=64, max_length=64)
    store_root: str = Field(min_length=1)
    database_path: str = Field(min_length=1)
    store_schema_version: int = Field(ge=1)
    environment_id: str = Field(min_length=1)
    evidence_run_ids: list[str] = Field(min_length=1)
    evidence_step_ids: list[str] = Field(min_length=1)
    observation_ids: list[str] = Field(min_length=1)
    semantic_state_ids: list[str] = Field(min_length=1)
    transition_edge_ids: list[str] = Field(default_factory=list)
    inserted_state_observation_count: int = Field(ge=0)
    inserted_state_assignment_count: int = Field(ge=0)
    inserted_semantic_state_version_count: int = Field(ge=0)
    inserted_transition_edge_version_count: int = Field(ge=0)
    volatile_ui_token_count: int = Field(ge=0)
    persistence_reopen_verified: bool


class LiveEvidenceStateAutoIngestResultV1(BaseModel):
    """Outer-loop result for idempotently ingesting the latest terminal runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "game-observatory.ai-player.live-evidence-state-auto-ingest-result.v1"
    ] = Field(
        default="game-observatory.ai-player.live-evidence-state-auto-ingest-result.v1",
        alias="schema",
    )
    status: Literal["ingested", "skipped"]
    reason: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    requested_evidence_run_ids: list[str] = Field(default_factory=list)
    eligible_evidence_run_ids: list[str] = Field(default_factory=list)
    seed_path: str | None = None
    seed_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    result_path: str | None = None
    result: LiveEvidenceStateIngestResultV1 | None = None


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("expected seed SHA-256 must be 64 hexadecimal characters")
    return normalized


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}.{digest}"


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _verified_artifact(
    observatory: ObservatoryStore,
    *,
    artifact_id: str,
    expected_kind: Literal["screenshot", "ui_tree"],
    run: EvidenceRun,
    step: EvidenceStep,
    expected_role: str,
    expected_environment_id: str,
) -> ArtifactRef:
    artifact = observatory.get_artifact(artifact_id)
    if artifact is None:
        raise ValueError(f"unknown canonical artifact: {artifact_id}")
    if artifact.kind != expected_kind:
        raise ValueError(
            f"artifact {artifact_id} kind is {artifact.kind}, expected {expected_kind}"
        )
    if artifact.id not in step.artifact_ids or artifact.id not in run.artifact_ids:
        raise ValueError(f"artifact is not retained by its EvidenceRun/EvidenceStep: {artifact.id}")
    path = Path(artifact.path).resolve()
    artifact_root = observatory.artifact_root.resolve()
    if not _inside(path, artifact_root):
        raise ValueError(f"artifact path escapes canonical Observatory root: {artifact.id}")
    if not path.is_file():
        raise ValueError(f"artifact path does not exist: {artifact.id}")
    if _sha256_bytes(path.read_bytes()) != artifact.sha256:
        raise ValueError(f"evidence artifact hash mismatch: {artifact.id}")
    observation_only = bool(step.metadata.get("observation_only"))
    reused_route_boundary = bool(
        expected_kind == "screenshot"
        and expected_role == "before"
        and step.metadata.get("reused_route_boundary_before") is True
        and step.metadata.get("reused_before_artifact_id") == artifact.id
    )
    if reused_route_boundary:
        guard = run.environment.get("source_state_guard")
        if not isinstance(guard, dict) or guard.get("artifact_id") != artifact.id:
            raise ValueError(
                f"reused route-boundary Before lacks the matching source guard: {artifact.id}"
            )
        prior_run_id = str(artifact.metadata.get("evidence_run_id") or "")
        prior_step_id = str(artifact.metadata.get("evidence_step_id") or "")
        prior_run = observatory.get_evidence_run(prior_run_id)
        prior_step = observatory.get_evidence_step(prior_step_id)
        if (
            prior_run is None
            or prior_run.target_id != run.target_id
            or prior_step is None
            or prior_step.evidence_run_id != prior_run.id
            or prior_step.status != "passed"
            or not prior_step.ended_at
            or prior_step.after_frame_id != artifact.id
            or artifact.metadata.get("evidence_role") != "after"
        ):
            raise ValueError(
                f"reused route-boundary Before is not a verified prior After: {artifact.id}"
            )
    expected_metadata = {
        "evidence_run_id": run.id,
        "evidence_step_id": step.id,
        "evidence_role": (
            "observation_ui_tree"
            if observation_only and expected_kind == "ui_tree"
            else "observation"
            if observation_only
            else expected_role
        ),
    }
    if not reused_route_boundary:
        for field_name, expected in expected_metadata.items():
            if artifact.metadata.get(field_name) != expected:
                raise ValueError(
                    f"artifact {field_name} does not match EvidenceStep role: {artifact.id}"
                )
    metadata_environment_id = artifact.metadata.get("environment_id")
    if metadata_environment_id and metadata_environment_id != expected_environment_id:
        raise ValueError(
            "cross-environment artifact metadata: "
            f"{artifact.id} belongs to {metadata_environment_id}"
        )
    if artifact.metadata.get("semantic_state_eligible") is False:
        raise ValueError(
            f"artifact is marked semantic_state_eligible=false: {artifact.id}"
        )
    return artifact


def _source_guard_state_id(
    observatory: ObservatoryStore,
    player: AIPlayerStore,
    *,
    environment_id: str,
    run: EvidenceRun,
    authoritative_artifact_state_ids: dict[str, str] | None = None,
) -> str | None:
    """Resolve a hash-current pre-action artifact to its existing semantic state."""

    guard = run.environment.get("source_state_guard")
    if guard is None:
        return None
    if not isinstance(guard, dict):
        raise ValueError(f"EvidenceRun source-state guard is malformed: {run.id}")
    artifact_id = guard.get("artifact_id")
    artifact_sha256 = guard.get("artifact_sha256")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError(f"EvidenceRun source-state guard lacks an artifact id: {run.id}")
    artifact = observatory.get_artifact(artifact_id)
    if artifact is None:
        raise ValueError(f"EvidenceRun source-state guard artifact is unknown: {run.id}")
    path = Path(artifact.path).resolve()
    artifact_root = observatory.artifact_root.resolve()
    if (
        artifact_sha256 != artifact.sha256
        or not _inside(path, artifact_root)
        or not path.is_file()
        or _sha256_bytes(path.read_bytes()) != artifact.sha256
    ):
        raise ValueError(f"EvidenceRun source-state guard is not hash-current: {run.id}")
    artifact_environment_id = artifact.metadata.get("environment_id")
    if artifact_environment_id and artifact_environment_id != environment_id:
        raise ValueError(f"EvidenceRun source-state guard crosses environments: {run.id}")

    # An ordered deterministic route is persisted as one batch.  The source guard
    # of action N+1 points at action N's verified After artifact, which has not yet
    # been written to the semantic graph.  Carry that verified state in memory so
    # batching keeps exact edge continuity.
    if authoritative_artifact_state_ids is not None:
        authoritative_state_id = authoritative_artifact_state_ids.get(artifact_id)
        if authoritative_state_id is not None:
            return authoritative_state_id

    declared_state_id = guard.get("semantic_state_id")
    declared_observation_id = guard.get("observation_id")
    binding_method = guard.get("binding_method")
    if declared_state_id is not None or declared_observation_id is not None:
        if (
            not isinstance(declared_state_id, str)
            or not declared_state_id
            or not isinstance(declared_observation_id, str)
            or not declared_observation_id
            or binding_method != "verified_skill_source_assignment"
        ):
            raise ValueError(f"EvidenceRun direct source-state binding is malformed: {run.id}")
        skill_version_id = run.environment.get("skill_replay_version_id")
        if not isinstance(skill_version_id, str) or not skill_version_id:
            raise ValueError(
                f"EvidenceRun direct source-state binding lacks a skill replay: {run.id}"
            )
        skill = player.get_skill_version_by_id(environment_id, skill_version_id)
        if skill is None:
            raise ValueError(
                f"EvidenceRun direct source-state binding has an unknown skill: {run.id}"
            )
        observation = player.get_state_observation(
            environment_id,
            declared_observation_id,
        )
        if observation is None or not any(
            artifact_id in reference.artifact_ids for reference in observation.evidence_refs
        ):
            raise ValueError(
                f"EvidenceRun direct source observation does not bind its artifact: {run.id}"
            )
        assignment = player.get_current_state_assignment(
            environment_id,
            declared_observation_id,
        )
        if (
            assignment is None
            or assignment.status != "active"
            or assignment.state_id != declared_state_id
        ):
            raise ValueError(
                f"EvidenceRun direct source assignment is no longer current: {run.id}"
            )
        state = player.get_semantic_state(environment_id, declared_state_id)
        if state is None or state.status not in {"candidate", "accepted"}:
            raise ValueError(
                f"EvidenceRun direct source state is unavailable: {run.id}"
            )
        return declared_state_id

    state_ids: list[str] = []
    accepted_state_ids: list[str] = []
    for assignment in player.list_state_assignments(environment_id):
        if assignment.status != "active":
            continue
        observation = player.get_state_observation(environment_id, assignment.observation_id)
        if observation is None or not any(
            artifact_id in reference.artifact_ids for reference in observation.evidence_refs
        ):
            continue
        state = player.get_semantic_state(environment_id, assignment.state_id)
        if state is None or state.status not in {"candidate", "accepted"}:
            continue
        state_ids.append(state.id)
        if state.status == "accepted":
            accepted_state_ids.append(state.id)
    preferred = list(dict.fromkeys(accepted_state_ids or state_ids))
    if not preferred:
        # A worker may take an extra read-only screenshot inside one provider turn
        # and immediately use it as the next action source.  Its hash guard is still
        # valid, but the observation is not yet in the semantic graph.  Let normal
        # recognition classify the before frame instead of discarding the action.
        return None
    if len(preferred) != 1:
        raise ValueError(
            f"EvidenceRun source-state guard artifact has conflicting state assignments: {run.id}"
        )
    return preferred[0]


def _expected_terminal_state_id(
    observatory: ObservatoryStore,
    player: AIPlayerStore,
    *,
    environment_id: str,
    run: EvidenceRun,
    after_frame: ArtifactRef,
) -> str | None:
    """Return an authoritative endpoint only when the terminal evidence matches.

    The declared endpoint is a replay expectation, not a replacement for the
    real device result.  A valid, hash-bound terminal screenshot that differs
    from the expectation must continue through normal state recognition so the
    mismatch becomes an explicit counterexample and later navigation can resume.
    """

    expected_state_id = run.environment.get("expected_semantic_state_id")
    if expected_state_id is None:
        return None
    if not isinstance(expected_state_id, str) or not expected_state_id:
        raise ValueError(f"EvidenceRun expected semantic state is malformed: {run.id}")
    state = player.get_semantic_state(environment_id, expected_state_id)
    if state is None or state.status not in {"candidate", "accepted"}:
        raise ValueError(f"EvidenceRun expected semantic state is unavailable: {run.id}")
    maximum = float(run.environment.get("expected_state_max_visual_distance") or 0.012)
    if maximum <= 0 or maximum > 0.03:
        raise ValueError(f"EvidenceRun expected-state visual threshold is invalid: {run.id}")

    comparison_paths: list[Path] = []
    for assignment in player.list_state_assignments(
        environment_id,
        state_id=expected_state_id,
    ):
        if assignment.status != "active":
            continue
        observation = player.get_state_observation(environment_id, assignment.observation_id)
        if observation is None:
            continue
        for reference in observation.evidence_refs:
            for artifact_id in reference.artifact_ids:
                artifact = observatory.get_artifact(artifact_id)
                if artifact is None or artifact.kind != "screenshot":
                    continue
                path = Path(artifact.path).resolve()
                if _inside(path, observatory.artifact_root.resolve()) and path.is_file():
                    comparison_paths.append(path)
    comparison_paths = list(dict.fromkeys(comparison_paths))[-20:]
    if not comparison_paths:
        raise ValueError(f"EvidenceRun expected semantic state has no screenshot prototype: {run.id}")
    target_bounds_payload = run.environment.get("expected_state_target_bounds")
    target_bounds = (
        SourcePixelRect.model_validate(target_bounds_payload)
        if isinstance(target_bounds_payload, dict)
        else None
    )
    for path in reversed(comparison_paths):
        global_distance = perceptual_frame_distance(after_frame.path, path)
        if global_distance <= maximum:
            return expected_state_id
        if (
            global_distance <= 0.03
            and structural_frame_distance(after_frame.path, path) <= 0.07
        ):
            return expected_state_id
        if target_bounds is not None:
            # Known game surfaces can contain character animation, lighting, and
            # account-specific content while their control structure stays fixed.
            # The broad-surface bound prevents a locally similar control from
            # authoritatively binding an unrelated caller screen.
            if global_distance <= 0.10:
                structural_distance = regional_structural_frame_distance(
                    after_frame.path,
                    path,
                    target_bounds,
                )
                if structural_distance <= 0.08:
                    return expected_state_id
    # Evidence integrity and environment identity were checked before this
    # comparison.  Falling back here preserves the actual terminal observation;
    # it does not claim that the skill reached its expected state.
    return None


def _verified_deterministic_route_binding(
    observatory: ObservatoryStore,
    player: AIPlayerStore,
    *,
    environment_id: str,
    run: EvidenceRun,
    step: EvidenceStep,
    verified_skill_run_id: str,
) -> tuple[StateObservationV1, str, str]:
    """Validate the signed fast-path proof and return its existing source binding.

    The physical executor has already compared the live Before frame with the
    canonical source and the terminal probe with the skill's trusted endpoint.
    This function does not weaken either guard: it re-hashes every retained frame,
    verifies the signed SkillRun and its canonical provenance, and requires the
    persisted EvidenceStep stability receipt to contain the successful terminal
    comparison.  Only the redundant semantic reconstruction is skipped.
    """

    skill_run = player.get_skill_run(environment_id, verified_skill_run_id)
    if skill_run is None:
        raise ValueError(
            f"guarded deterministic ingest references a missing SkillRun: "
            f"{verified_skill_run_id}"
        )
    player.verify_skill_run_provenance(skill_run)
    if (
        skill_run.outcome != "success"
        or not skill_run.precondition_satisfied
        or not skill_run.objective_success
        or not skill_run.validation_passed
        or skill_run.false_success
        or skill_run.safety_violation_count
    ):
        raise ValueError(
            f"guarded deterministic ingest requires a successful signed SkillRun: "
            f"{skill_run.id}"
        )
    if skill_run.skill_version_id != run.environment.get("skill_replay_version_id"):
        raise ValueError(
            f"guarded deterministic ingest skill version mismatch: {run.id}"
        )
    referenced_run_ids = {
        item
        for reference in skill_run.evidence_refs
        for item in reference.evidence_run_ids
    }
    referenced_step_ids = {
        item
        for reference in skill_run.evidence_refs
        for item in reference.evidence_step_ids
    }
    if run.id not in referenced_run_ids or step.id not in referenced_step_ids:
        raise ValueError(
            f"guarded deterministic ingest is not covered by its signed SkillRun: {run.id}"
        )

    guard = run.environment.get("source_state_guard")
    if not isinstance(guard, dict):
        raise ValueError(f"guarded deterministic ingest lacks a source guard: {run.id}")
    source_state_id = guard.get("semantic_state_id")
    source_observation_id = guard.get("observation_id")
    if (
        not isinstance(source_state_id, str)
        or not source_state_id
        or not isinstance(source_observation_id, str)
        or not source_observation_id
        or guard.get("binding_method") != "verified_skill_source_assignment"
    ):
        raise ValueError(
            f"guarded deterministic ingest lacks an existing source assignment: {run.id}"
        )
    verified_source_state_id = _source_guard_state_id(
        observatory,
        player,
        environment_id=environment_id,
        run=run,
    )
    if verified_source_state_id != source_state_id:
        raise ValueError(
            f"guarded deterministic ingest source assignment changed: {run.id}"
        )
    source_observation = player.get_state_observation(
        environment_id,
        source_observation_id,
    )
    if source_observation is None:
        raise ValueError(
            f"guarded deterministic ingest source observation is missing: {run.id}"
        )

    terminal_state_id = run.environment.get("expected_semantic_state_id")
    expected_reference_id = run.environment.get("expected_state_reference_artifact_id")
    if (
        not isinstance(terminal_state_id, str)
        or not terminal_state_id
        or not isinstance(expected_reference_id, str)
        or not expected_reference_id
    ):
        raise ValueError(
            f"guarded deterministic ingest lacks a declared terminal guard: {run.id}"
        )
    skill = player.get_skill_version_by_id(environment_id, skill_run.skill_version_id)
    if skill is None or terminal_state_id not in {
        item.expected_state_id
        for item in skill.steps
        if item.kind == "assert" and item.expected_state_id is not None
    }:
        raise ValueError(
            f"guarded deterministic ingest terminal is outside the signed skill contract: "
            f"{run.id}"
        )
    terminal_state = player.get_semantic_state(environment_id, terminal_state_id)
    if terminal_state is None or terminal_state.status not in {"candidate", "accepted"}:
        raise ValueError(
            f"guarded deterministic ingest terminal state is unavailable: {run.id}"
        )
    stability = step.stability
    if (
        not stability.settled
        or stability.trusted_reference_artifact_id != expected_reference_id
        or stability.trusted_reference_matched is not True
        or stability.trusted_reference_distance is None
        or stability.trusted_reference_max_distance is None
        or stability.trusted_reference_distance > stability.trusted_reference_max_distance
    ):
        raise ValueError(
            f"guarded deterministic ingest terminal visual guard did not pass: {run.id}"
        )
    declared_maximum = float(run.environment.get("expected_state_max_visual_distance") or 0)
    if (
        declared_maximum <= 0
        or declared_maximum > 0.03
        or stability.trusted_reference_max_distance != declared_maximum
    ):
        raise ValueError(
            f"guarded deterministic ingest terminal threshold drifted: {run.id}"
        )
    reference = observatory.get_artifact(expected_reference_id)
    if reference is None or reference.kind != "screenshot":
        raise ValueError(
            f"guarded deterministic ingest terminal reference is missing: {run.id}"
        )
    reference_path = Path(reference.path).resolve()
    if (
        not _inside(reference_path, observatory.artifact_root.resolve())
        or not reference_path.is_file()
        or _sha256_bytes(reference_path.read_bytes()) != reference.sha256
    ):
        raise ValueError(
            f"guarded deterministic ingest terminal reference hash changed: {run.id}"
        )
    return source_observation, source_state_id, terminal_state_id


def _require_evidence_environment_binding(
    observatory: ObservatoryStore,
    player: AIPlayerStore,
    *,
    environment_id: str,
    run: EvidenceRun,
    step: EvidenceStep,
) -> None:
    """Bind a legacy evidence bundle without trusting endpoint or seed declarations."""

    explicit_environment_ids: list[tuple[str, str]] = []
    run_environment_id = run.environment.get("environment_id")
    if run_environment_id:
        explicit_environment_ids.append((f"EvidenceRun:{run.id}", run_environment_id))

    for artifact_id in step.artifact_ids:
        artifact = observatory.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"unknown canonical artifact: {artifact_id}")
        if artifact.id not in run.artifact_ids:
            raise ValueError(
                f"artifact is not retained by its EvidenceRun/EvidenceStep: {artifact.id}"
            )
        artifact_environment_id = artifact.metadata.get("environment_id")
        if artifact_environment_id:
            explicit_environment_ids.append(
                (f"artifact:{artifact.id}", artifact_environment_id)
            )

    mismatches = [
        f"{source}={observed}"
        for source, observed in explicit_environment_ids
        if observed != environment_id
    ]
    if mismatches:
        raise ValueError(
            "cross-environment live evidence identity: " + "; ".join(mismatches)
        )
    if explicit_environment_ids:
        return

    if player.has_exact_persisted_evidence_binding(
        environment_id,
        evidence_run_id=run.id,
        evidence_step_id=step.id,
        artifact_ids=step.artifact_ids,
    ):
        return
    raise ValueError(
        "live evidence lacks exact environment identity or a canonical persisted "
        f"run/step binding: {environment_id}/{run.id}/{step.id}"
    )


def _dhash(path: Path) -> str:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot decode screenshot: {path}")
    resized = cv2.resize(image, (17, 16), interpolation=cv2.INTER_AREA)
    bits = (resized[:, 1:] >= resized[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:064x}"


def _region_hashes(path: Path) -> dict[str, str]:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot decode screenshot: {path}")
    height, width = image.shape[:2]
    output: dict[str, str] = {}
    for row in range(3):
        for column in range(3):
            crop = image[
                row * height // 3 : (row + 1) * height // 3,
                column * width // 3 : (column + 1) * width // 3,
            ]
            resized = cv2.resize(crop, (8, 8), interpolation=cv2.INTER_AREA)
            bits = (resized >= float(resized.mean())).flatten()
            value = 0
            for bit in bits:
                value = (value << 1) | int(bit)
            output[f"grid-{row}-{column}"] = f"{value:016x}"
    return output


def _text_tokens(attribute: str, value: str) -> tuple[str, str | None]:
    normalized = re.sub(r"\s+", " ", value.strip())
    if not _NUMBER_PATTERN.search(normalized):
        return f"{attribute}:{normalized}", None
    masked = _NUMBER_PATTERN.sub("<number>", normalized)
    volatile_kind = "numeric"
    if _TIMER_PATTERN.search(normalized):
        volatile_kind = "timer"
    elif any(word in normalized for word in _RESOURCE_WORDS):
        volatile_kind = "resource"
    return (
        f"{attribute}:{masked}",
        f"volatile-ui-{volatile_kind}:{attribute}:{normalized}",
    )


def _ui_features(path: Path) -> tuple[list[str], list[str], list[str], list[str], list[str], dict[str, str]]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"cannot parse UI tree: {path}") from error
    structure: set[str] = set()
    text: set[str] = set()
    selected: set[str] = set()
    overlays: set[str] = set()
    volatile: set[str] = set()
    packages: set[str] = set()
    root_class = root.attrib.get("class") or root.tag

    def visit(node: ET.Element, depth: int) -> None:
        class_name = node.attrib.get("class") or node.tag
        resource_id = node.attrib.get("resource-id", "").strip()
        package = node.attrib.get("package", "").strip()
        if package:
            packages.add(package)
        structure.add(
            f"depth:{depth}|class:{class_name}|resource-id:{resource_id or '<none>'}"
        )
        stable_values: list[str] = []
        for attribute in ("text", "content-desc", "hint"):
            value = node.attrib.get(attribute, "").strip()
            if not value:
                continue
            stable, changing = _text_tokens(attribute, value)
            text.add(stable)
            stable_values.append(stable)
            if changing:
                volatile.add(changing)
        if node.attrib.get("selected") == "true":
            identity = resource_id or (stable_values[0] if stable_values else class_name)
            selected.add(identity)
        if "dialog" in class_name.casefold() or "dialog" in resource_id.casefold():
            overlays.add(resource_id or class_name)
        for child in node:
            visit(child, depth + 1)

    visit(root, 0)
    critical = {"ui-root-class": root_class}
    if len(packages) == 1:
        critical["ui-package"] = next(iter(packages))
    return (
        sorted(structure),
        sorted(text),
        sorted(selected),
        sorted(overlays),
        sorted(volatile),
        critical,
    )


def _observation_features(
    screenshot: ArtifactRef,
    ui_tree: ArtifactRef | None,
    *,
    run: EvidenceRun,
    environment_channel: str,
    surface_profile: SemanticSurfaceProfileV1 | None = None,
) -> StateObservationFeaturesV1:
    screenshot_path = Path(screenshot.path).resolve()
    decoded = cv2.imread(str(screenshot_path), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise ValueError(f"cannot decode screenshot: {screenshot.id}")
    height, width = decoded.shape[:2]
    if (width, height) != (run.viewport_width, run.viewport_height):
        raise ValueError(
            f"screenshot viewport mismatch: {screenshot.id} is {width}x{height}, "
            f"expected {run.viewport_width}x{run.viewport_height}"
        )
    if ui_tree is None:
        structure: list[str] = []
        text: list[str] = []
        selected: list[str] = []
        overlays: list[str] = []
        volatile: list[str] = []
        critical = {"evidence-profile": "screenshot-only"}
    else:
        structure, text, selected, overlays, volatile, critical = _ui_features(
            Path(ui_tree.path).resolve()
        )
    return StateObservationFeaturesV1(
        screenshot_fingerprint=_dhash(screenshot_path),
        ui_structure_tokens=structure,
        ui_text_tokens=text,
        runtime_tokens=[
            f"game:{run.game_id}",
            f"build:{run.build_scope_id}",
            f"channel:{environment_channel}",
            f"adapter:{run.adapter}",
            f"viewport:{run.viewport_width}x{run.viewport_height}",
            f"orientation:{run.orientation}",
        ],
        selected_object_tokens=selected,
        overlay_tokens=overlays,
        page_identity_tokens=(
            surface_profile.page_identity_tokens if surface_profile is not None else []
        ),
        dynamic_field_names=(
            surface_profile.dynamic_field_names if surface_profile is not None else []
        ),
        interaction_roles=(
            surface_profile.interaction_roles if surface_profile is not None else []
        ),
        safe_exit_tokens=(
            surface_profile.safe_exit_tokens if surface_profile is not None else []
        ),
        risk_boundary_tokens=(
            surface_profile.risk_boundary_tokens if surface_profile is not None else []
        ),
        region_fingerprints=_region_hashes(screenshot_path),
        critical_features=critical,
        volatile_tokens=volatile,
    )


def _semantic_surface_profile(
    step: EvidenceStep,
    frame: ArtifactRef,
    ui_tree: ArtifactRef | None,
    *,
    role: str,
) -> SemanticSurfaceProfileV1 | None:
    """Resolve one role-bound profile without guessing from UI content.

    Step metadata is the canonical live-step producer. Artifact metadata remains a
    supported capture-adapter boundary. Multiple declarations must agree exactly.
    """

    canonical_role = "after" if role == "observation" else role
    candidates: list[tuple[str, SemanticSurfaceProfileV1]] = []
    raw_profiles = step.metadata.get("semantic_surface_profiles")
    if raw_profiles is not None:
        if not isinstance(raw_profiles, Mapping) or set(raw_profiles) != {
            "before",
            "after",
        }:
            raise ValueError(
                "EvidenceStep semantic_surface_profiles must contain exactly "
                f"before and after roles: {step.id}"
            )
        candidates.append(
            (
                f"step:{step.id}:{canonical_role}",
                SemanticSurfaceProfileV1.model_validate(raw_profiles[canonical_role]),
            )
        )
    for artifact in (frame, ui_tree):
        if artifact is None:
            continue
        raw_profile = artifact.metadata.get("semantic_surface_profile")
        if raw_profile is not None:
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


def _extract_seed_entities(
    observatory: ObservatoryStore,
    player: AIPlayerStore,
    seed: LiveEvidenceStateIngestSeedV1,
) -> tuple[list[StateObservationV1], list[StateTransitionIntent], list[EvidenceReferenceV1]]:
    environment = player.get_environment(seed.environment_id)
    if environment is None:
        raise KeyError(f"unknown AI-player environment: {seed.environment_id}")
    selection = player.select_environment_lineage(seed.environment_id)
    if selection.selected_environment_id != seed.environment_id:
        raise ValueError(
            "live evidence must be ingested into the current environment leaf: "
            f"{seed.environment_id} -> {selection.selected_environment_id}"
        )

    observations: list[StateObservationV1] = []
    observation_ids: set[str] = set()
    intents: list[StateTransitionIntent] = []
    references: list[EvidenceReferenceV1] = []
    authoritative_artifact_state_ids: dict[str, str] = {}
    for run_selection in seed.runs:
        run = observatory.get_evidence_run(run_selection.evidence_run_id)
        if run is None:
            raise ValueError(f"unknown EvidenceRun: {run_selection.evidence_run_id}")
        if run.status != "passed" or not run.ended_at:
            raise ValueError(f"EvidenceRun is not terminal-passed: {run.id}")
        if run.environment.get("semantic_state_eligible") is False:
            raise ValueError(
                f"EvidenceRun is marked semantic_state_eligible=false: {run.id}"
            )
        if tuple(run.step_ids) != run_selection.evidence_step_ids:
            raise ValueError(
                f"seed must retain the complete ordered EvidenceRun step list: {run.id}"
            )
        if (run.viewport_width, run.viewport_height) != (
            environment.viewport_width,
            environment.viewport_height,
        ):
            raise ValueError(f"EvidenceRun viewport does not match environment: {run.id}")
        authoritative_before_state_id = _source_guard_state_id(
            observatory,
            player,
            environment_id=environment.id,
            run=run,
            authoritative_artifact_state_ids=authoritative_artifact_state_ids,
        )

        for step_id in run_selection.evidence_step_ids:
            step = observatory.get_evidence_step(step_id)
            if step is None:
                raise ValueError(f"unknown EvidenceStep: {step_id}")
            if step.evidence_run_id != run.id or step.id not in run.step_ids:
                raise ValueError(f"EvidenceRun/EvidenceStep relationship mismatch: {step.id}")
            if step.status != "passed" or not step.ended_at:
                raise ValueError(f"EvidenceStep is not terminal-passed: {step.id}")
            if step.action.type in LIFECYCLE_ACTION_TYPES:
                raise ValueError(
                    f"lifecycle EvidenceStep cannot enter semantic state ingest: {step.id}"
                )
            if step.metadata.get("semantic_state_eligible") is False:
                raise ValueError(f"EvidenceStep is marked semantic_state_eligible=false: {step.id}")
            _require_evidence_environment_binding(
                observatory,
                player,
                environment_id=environment.id,
                run=run,
                step=step,
            )
            if step.publication_issues():
                raise ValueError(
                    f"EvidenceStep is not publication-complete: {step.id}: "
                    + "; ".join(step.publication_issues())
                )
            if (step.viewport_width, step.viewport_height) != (
                run.viewport_width,
                run.viewport_height,
            ):
                raise ValueError(f"EvidenceStep viewport does not match its run: {step.id}")
            required_screenshot_ids = {
                "before_frame_id": step.before_frame_id,
                "after_frame_id": step.after_frame_id,
            }
            missing_roles = [
                role for role, artifact_id in required_screenshot_ids.items() if not artifact_id
            ]
            if missing_roles:
                raise ValueError(
                    f"EvidenceStep lacks required screenshot roles: {step.id}: {missing_roles}"
                )
            if bool(step.before_ui_tree_id) != bool(step.after_ui_tree_id):
                raise ValueError(
                    "EvidenceStep must retain both UI-tree roles or neither: "
                    f"{step.id}"
                )

            before_frame = _verified_artifact(
                observatory,
                artifact_id=str(step.before_frame_id),
                expected_kind="screenshot",
                run=run,
                step=step,
                expected_role="before",
                expected_environment_id=environment.id,
            )
            before_ui = (
                _verified_artifact(
                    observatory,
                    artifact_id=str(step.before_ui_tree_id),
                    expected_kind="ui_tree",
                    run=run,
                    step=step,
                    expected_role="before_ui_tree",
                    expected_environment_id=environment.id,
                )
                if step.before_ui_tree_id
                else None
            )
            after_frame = _verified_artifact(
                observatory,
                artifact_id=str(step.after_frame_id),
                expected_kind="screenshot",
                run=run,
                step=step,
                expected_role="after",
                expected_environment_id=environment.id,
            )
            after_ui = (
                _verified_artifact(
                    observatory,
                    artifact_id=str(step.after_ui_tree_id),
                    expected_kind="ui_tree",
                    run=run,
                    step=step,
                    expected_role="after_ui_tree",
                    expected_environment_id=environment.id,
                )
                if step.after_ui_tree_id
                else None
            )
            deterministic_binding = (
                _verified_deterministic_route_binding(
                    observatory,
                    player,
                    environment_id=environment.id,
                    run=run,
                    step=step,
                    verified_skill_run_id=run_selection.verified_skill_run_id,
                )
                if run_selection.verified_skill_run_id is not None
                else None
            )
            if deterministic_binding is not None:
                source_observation, bound_source_state_id, authoritative_after_state_id = (
                    deterministic_binding
                )
                if bound_source_state_id != authoritative_before_state_id:
                    raise ValueError(
                        f"guarded deterministic source binding changed: {run.id}"
                    )
                if source_observation.id not in observation_ids:
                    observations.append(source_observation)
                    observation_ids.add(source_observation.id)
                references.extend(source_observation.evidence_refs)
            else:
                source_observation = None
                authoritative_after_state_id = _expected_terminal_state_id(
                    observatory,
                    player,
                    environment_id=environment.id,
                    run=run,
                    after_frame=after_frame,
                )
            if authoritative_after_state_id is not None:
                authoritative_artifact_state_ids[after_frame.id] = (
                    authoritative_after_state_id
                )
            role_artifacts = (
                {"observation": (after_frame, after_ui, step.ended_at)}
                if step.metadata.get("observation_only")
                else {"after": (after_frame, after_ui, step.ended_at)}
                if source_observation is not None
                else {
                    "before": (before_frame, before_ui, step.started_at),
                    "after": (after_frame, after_ui, step.ended_at),
                }
            )
            step_observation_ids: dict[str, str] = {}
            if source_observation is not None:
                step_observation_ids["before"] = source_observation.id
            step_references: list[EvidenceReferenceV1] = []
            for role, (frame, ui_tree, captured_at) in role_artifacts.items():
                bound_artifacts = [frame, *([ui_tree] if ui_tree is not None else [])]
                bound_run_ids = list(
                    dict.fromkeys(
                        [
                            run.id,
                            *[
                                str(artifact.metadata.get("evidence_run_id") or "")
                                for artifact in bound_artifacts
                            ],
                        ]
                    )
                )
                bound_step_ids = list(
                    dict.fromkeys(
                        [
                            step.id,
                            *[
                                str(artifact.metadata.get("evidence_step_id") or "")
                                for artifact in bound_artifacts
                            ],
                        ]
                    )
                )
                reference = EvidenceReferenceV1(
                    environment_id=seed.environment_id,
                    artifact_ids=[frame.id, *([ui_tree.id] if ui_tree is not None else [])],
                    evidence_run_ids=[item for item in bound_run_ids if item],
                    evidence_step_ids=[item for item in bound_step_ids if item],
                    note=(
                        f"{step.id} 的 {role} 截图与 UI 树。"
                        if ui_tree is not None
                        else f"{step.id} 的 {role} 轻量截图证据。"
                    ),
                )
                features = _observation_features(
                    frame,
                    ui_tree,
                    run=run,
                    environment_channel=environment.channel,
                    surface_profile=resolve_semantic_surface_profile(
                        step,
                        [frame, *([ui_tree] if ui_tree is not None else [])],
                        role=role,
                    ),
                )
                observation_id = _stable_id(
                    "observation.evidence",
                    seed.environment_id,
                    run.id,
                    step.id,
                    role,
                )
                observation = build_state_observation(
                    environment_id=seed.environment_id,
                    viewport_width=run.viewport_width,
                    viewport_height=run.viewport_height,
                    features=features,
                    evidence_refs=[reference],
                    observation_id=observation_id,
                    captured_at=captured_at,
                    created_at=captured_at,
                )
                if observation.id not in observation_ids:
                    observations.append(observation)
                    observation_ids.add(observation.id)
                references.append(reference)
                step_references.append(reference)
                step_observation_ids[role] = observation.id

            if step.metadata.get("observation_only"):
                player.resolve_evidence_references(
                    step_references,
                    environment_scope=environment,
                )
                continue

            edge_artifacts = [
                before_frame,
                *([before_ui] if before_ui is not None else []),
                after_frame,
                *([after_ui] if after_ui is not None else []),
            ]
            edge_run_ids = list(
                dict.fromkeys(
                    [
                        run.id,
                        *[
                            str(artifact.metadata.get("evidence_run_id") or "")
                            for artifact in edge_artifacts
                        ],
                    ]
                )
            )
            edge_step_ids = list(
                dict.fromkeys(
                    [
                        step.id,
                        *[
                            str(artifact.metadata.get("evidence_step_id") or "")
                            for artifact in edge_artifacts
                        ],
                    ]
                )
            )
            edge_reference = EvidenceReferenceV1(
                environment_id=seed.environment_id,
                artifact_ids=[artifact.id for artifact in edge_artifacts],
                evidence_run_ids=[item for item in edge_run_ids if item],
                evidence_step_ids=[item for item in edge_step_ids if item],
                note=f"{step.id} 的完整 Before/Action/After 证据。",
            )
            target = step.target_name or step.action.type
            intents.append(
                StateTransitionIntent(
                    id=_stable_id(
                        "edge.evidence",
                        seed.environment_id,
                        run.id,
                        step.id,
                    ),
                    before_observation_id=step_observation_ids["before"],
                    after_observation_id=step_observation_ids["after"],
                    action=step.action,
                    target_bounds=step.target_bounds,
                    expected_change=f"执行 {step.action.type}（{target}）后重新识别界面状态",
                    evidence_refs=(edge_reference,),
                    created_at=step.ended_at,
                    authoritative_before_state_id=authoritative_before_state_id,
                    authoritative_after_state_id=authoritative_after_state_id,
                )
            )
            references.append(edge_reference)
            player.resolve_evidence_references(
                [*step_references, edge_reference],
                environment_scope=environment,
            )
    return observations, intents, references


def role_reference_values(
    _role_artifacts: dict[str, tuple[ArtifactRef, ArtifactRef, str]],
    references: Sequence[EvidenceReferenceV1],
) -> list[EvidenceReferenceV1]:
    """Keep the per-step evidence resolution call explicit and easy to audit."""

    return list(references)


def ingest_live_evidence_state_seed(
    store_root: Path,
    seed_path: Path,
    *,
    expected_store_root: Path,
    expected_seed_sha256: str,
) -> LiveEvidenceStateIngestResultV1:
    """Hash-check, extract, classify, atomically persist, reopen, and verify."""

    resolved_root = store_root.resolve()
    resolved_expected_root = expected_store_root.resolve()
    if resolved_root != resolved_expected_root:
        raise ValueError(
            "AI-player store root mismatch: "
            f"expected {resolved_expected_root}, received {resolved_root}"
        )
    if not resolved_root.is_dir():
        raise FileNotFoundError(f"AI-player store root is missing: {resolved_root}")
    seed_bytes = seed_path.read_bytes()
    seed_sha256 = _sha256_bytes(seed_bytes)
    if seed_sha256 != _validate_sha256(expected_seed_sha256):
        raise ValueError("live evidence state ingest seed SHA-256 mismatch")
    seed = LiveEvidenceStateIngestSeedV1.model_validate_json(seed_bytes)

    observatory = ObservatoryStore(resolved_root)
    guarded = any(item.verified_skill_run_id is not None for item in seed.runs)
    if guarded:
        _signer, trust_store = skill_runtime_signer_and_trust_store(resolved_root)
        player = AIPlayerStore(
            observatory,
            skill_validator_trust_store=trust_store,
        )
    else:
        player = AIPlayerStore(observatory)
    with observatory.read_session(), player.read_session():
        observations, intents, references = _extract_seed_entities(observatory, player, seed)
    applied = player.apply_state_transition_ingest(
        seed.environment_id,
        observations,
        intents,
    )
    decisions = applied["decisions"]
    edges = applied["edges"]

    reopened_observatory = ObservatoryStore(resolved_root)
    reopened = AIPlayerStore(reopened_observatory)
    with reopened_observatory.read_session(), reopened.read_session():
        selection = reopened.select_environment_lineage(seed.environment_id)
        persistence_verified = (
            _sha256_bytes(seed_path.read_bytes()) == seed_sha256
            and selection.selected_environment_id == seed.environment_id
            and all(
                reopened.get_state_observation(seed.environment_id, item.id) == item
                for item in observations
            )
            and all(
                (
                    assignment := reopened.get_current_state_assignment(
                        seed.environment_id,
                        observation.id,
                    )
                )
                is not None
                and assignment.id == decisions[observation.id].assignment_id
                and assignment.state_id == decisions[observation.id].state_id
                for observation in observations
            )
            and all(
                (
                    state := reopened.get_semantic_state(
                        seed.environment_id,
                        decisions[observation.id].state_id,
                    )
                )
                is not None
                and observation.feature_hash in state.observation_feature_hashes
                for observation in observations
            )
            and all(
                reopened.get_transition_edge(seed.environment_id, edge.id) == edge
                for edge in edges
            )
        )
        reopened.resolve_evidence_references(
            references,
            environment_scope=reopened.get_environment(seed.environment_id),
        )
        for run_selection in seed.runs:
            run = reopened_observatory.get_evidence_run(run_selection.evidence_run_id)
            if run is None or run.status != "passed" or not run.ended_at:
                persistence_verified = False
            for step_id in run_selection.evidence_step_ids:
                step = reopened_observatory.get_evidence_step(step_id)
                if step is None or step.status != "passed" or not step.ended_at:
                    persistence_verified = False


    return LiveEvidenceStateIngestResultV1(
        seed_id=seed.seed_id,
        seed_sha256=seed_sha256,
        store_root=str(resolved_root),
        database_path=str(reopened_observatory.db_path.resolve()),
        store_schema_version=reopened.schema_version,
        environment_id=seed.environment_id,
        evidence_run_ids=[item.evidence_run_id for item in seed.runs],
        evidence_step_ids=[
            step_id for item in seed.runs for step_id in item.evidence_step_ids
        ],
        observation_ids=[item.id for item in observations],
        semantic_state_ids=sorted(
            {decision.state_id for decision in decisions.values()}
        ),
        transition_edge_ids=[item.id for item in edges],
        inserted_state_observation_count=applied["inserted_state_observation_count"],
        inserted_state_assignment_count=applied["inserted_state_assignment_count"],
        inserted_semantic_state_version_count=applied[
            "inserted_semantic_state_version_count"
        ],
        inserted_transition_edge_version_count=applied[
            "inserted_transition_edge_version_count"
        ],
        volatile_ui_token_count=sum(
            len(item.features.volatile_tokens) for item in observations
        ),
        persistence_reopen_verified=persistence_verified,
    )


def auto_ingest_terminal_evidence_runs(
    store_root: Path,
    *,
    environment_id: str,
    evidence_run_ids: Sequence[str],
    verified_skill_run_ids: Mapping[str, str] | None = None,
) -> LiveEvidenceStateAutoIngestResultV1:
    """Persist and ingest eligible terminal runs without another model/tool turn.

    The generated seed and result stay beside the canonical Observatory store so an
    outer continuous-session wrapper can make state sedimentation automatic while
    retaining the same hash-locked and reopen-verified boundary as manual ingest.
    """

    requested_run_ids = list(dict.fromkeys(item.strip() for item in evidence_run_ids if item.strip()))
    if not requested_run_ids:
        return LiveEvidenceStateAutoIngestResultV1(
            status="skipped",
            reason="当前连续探索轮没有新的 EvidenceRun。",
            environment_id=environment_id,
        )

    resolved_root = store_root.resolve()
    observatory = ObservatoryStore(resolved_root)
    player = AIPlayerStore(observatory)
    environment = player.get_environment(environment_id)
    if environment is None:
        raise KeyError(f"unknown AI-player environment: {environment_id}")

    guarded_run_ids = dict(verified_skill_run_ids or {})
    unexpected_guarded_runs = sorted(set(guarded_run_ids) - set(requested_run_ids))
    if unexpected_guarded_runs:
        raise ValueError(
            "guarded deterministic bindings include unrequested EvidenceRuns: "
            + ", ".join(unexpected_guarded_runs)
        )
    eligible_runs: list[EvidenceRunSelectionV1] = []
    skipped_reasons: list[str] = []
    for run_id in requested_run_ids:
        run = observatory.get_evidence_run(run_id)
        if run is None:
            raise ValueError(f"unknown EvidenceRun: {run_id}")
        if run.status != "passed" or not run.ended_at:
            raise ValueError(f"EvidenceRun is not terminal-passed: {run.id}")
        if run.environment.get("semantic_state_eligible") is False:
            skipped_reasons.append(f"{run.id}: run 标记为不可进入语义状态图")
            continue
        steps: list[EvidenceStep] = []
        for step_id in run.step_ids:
            step = observatory.get_evidence_step(step_id)
            if step is None:
                raise ValueError(f"unknown EvidenceStep: {step_id}")
            if step.evidence_run_id != run.id:
                raise ValueError(f"EvidenceRun/EvidenceStep relationship mismatch: {step.id}")
            steps.append(step)
        if any(step.action.type in LIFECYCLE_ACTION_TYPES for step in steps):
            skipped_reasons.append(f"{run.id}: 生命周期动作不构成游戏内语义转移")
            continue
        if any(step.metadata.get("semantic_state_eligible") is False for step in steps):
            skipped_reasons.append(f"{run.id}: step 标记为不可进入语义状态图")
            continue
        eligible_runs.append(
            EvidenceRunSelectionV1(
                evidence_run_id=run.id,
                evidence_step_ids=tuple(run.step_ids),
                verified_skill_run_id=guarded_run_ids.get(run.id),
            )
        )

    if not eligible_runs:
        return LiveEvidenceStateAutoIngestResultV1(
            status="skipped",
            reason="；".join(skipped_reasons) or "没有可进入语义状态图的 EvidenceRun。",
            environment_id=environment_id,
            requested_evidence_run_ids=requested_run_ids,
        )

    identity = json.dumps(
        {
            "environment_id": environment_id,
            "runs": [item.model_dump(mode="json") for item in eligible_runs],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    seed_identity = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    seed = LiveEvidenceStateIngestSeedV1(
        seed_id=f"seed.live-state.auto.{seed_identity}",
        environment_id=environment_id,
        runs=tuple(eligible_runs),
    )
    seed_bytes = (
        json.dumps(
            seed.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    seed_sha256 = _sha256_bytes(seed_bytes)
    input_dir = resolved_root / "ai_player_inputs" / "live_state"
    result_dir = resolved_root / "results" / "live_state"
    input_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    seed_path = input_dir / f"{seed.seed_id}.json"
    if seed_path.exists() and seed_path.read_bytes() != seed_bytes:
        raise ValueError(f"deterministic live-state seed path has drifted: {seed_path}")
    if not seed_path.exists():
        seed_path.write_bytes(seed_bytes)

    result = ingest_live_evidence_state_seed(
        resolved_root,
        seed_path,
        expected_store_root=resolved_root,
        expected_seed_sha256=seed_sha256,
    )
    if not result.persistence_reopen_verified:
        raise ValueError("live-state auto ingest failed persistence reopen verification")
    result_path = result_dir / f"{seed.seed_id}.result.json"
    result_bytes = (
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    if result_path.exists() and result_path.read_bytes() != result_bytes:
        # A deferred edge can legitimately become verified after adjudication. Keep the
        # deterministic seed, and replace its materialized latest result atomically.
        temporary_path = result_path.with_suffix(f"{result_path.suffix}.tmp")
        temporary_path.write_bytes(result_bytes)
        temporary_path.replace(result_path)
    elif not result_path.exists():
        result_path.write_bytes(result_bytes)

    return LiveEvidenceStateAutoIngestResultV1(
        status="ingested",
        reason="终态动作证据已幂等沉淀为状态观察、候选状态和转移边。",
        environment_id=environment_id,
        requested_evidence_run_ids=requested_run_ids,
        eligible_evidence_run_ids=[item.evidence_run_id for item in eligible_runs],
        seed_path=str(seed_path),
        seed_sha256=seed_sha256,
        result_path=str(result_path),
        result=result,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--expected-store-root", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--expected-seed-sha256", required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = ingest_live_evidence_state_seed(
        args.store_root,
        args.seed,
        expected_store_root=args.expected_store_root,
        expected_seed_sha256=args.expected_seed_sha256,
    )
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return int(not result.persistence_reopen_verified)


if __name__ == "__main__":
    raise SystemExit(main())
