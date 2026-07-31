"""Evidence-backed semantic state recognition and adjudication."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..models import utc_now
from .contracts import (
    EvidenceReferenceV1,
    SemanticStateV1,
    StateAssignmentV1,
    StateMatchV1,
    StateObservationFeaturesV1,
    StateObservationV1,
    StateRecognitionDecisionV1,
)
from .store import AIPlayerStore


_DEFAULT_VOLATILE_PREFIXES = (
    "animation-frame:",
    "countdown:",
    "fps:",
    "server-time:",
    "timer:",
)


class StateSplitPartitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    observation_ids: tuple[str, ...] = Field(min_length=1)
    tags: tuple[str, ...] = ()


def _normalize_token(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _stable_tokens(values: Iterable[str], volatile: set[str]) -> tuple[str, ...]:
    tokens = {
        token
        for raw in values
        if (token := _normalize_token(raw))
        and token not in volatile
        and not token.startswith(_DEFAULT_VOLATILE_PREFIXES)
    }
    return tuple(sorted(tokens))


def stable_feature_payload(features: StateObservationFeaturesV1) -> dict[str, object]:
    volatile = {_normalize_token(item) for item in features.volatile_tokens}
    return {
        "ui_structure_tokens": _stable_tokens(features.ui_structure_tokens, volatile),
        "ui_text_tokens": _stable_tokens(features.ui_text_tokens, volatile),
        "runtime_tokens": _stable_tokens(features.runtime_tokens, volatile),
        "selected_object_tokens": _stable_tokens(features.selected_object_tokens, volatile),
        "overlay_tokens": _stable_tokens(features.overlay_tokens, volatile),
        "region_fingerprints": {
            _normalize_token(key): _normalize_token(value)
            for key, value in sorted(features.region_fingerprints.items())
        },
        "critical_features": {
            _normalize_token(key): _normalize_token(value)
            for key, value in sorted(features.critical_features.items())
        },
    }


def semantic_feature_hash(features: StateObservationFeaturesV1) -> str:
    payload = json.dumps(
        stable_feature_payload(features),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_state_observation(
    *,
    environment_id: str,
    viewport_width: int,
    viewport_height: int,
    features: StateObservationFeaturesV1,
    evidence_refs: Sequence[EvidenceReferenceV1],
    observation_id: str | None = None,
    captured_at: str | None = None,
) -> StateObservationV1:
    return StateObservationV1(
        id=observation_id or f"observation.{uuid4().hex}",
        environment_id=environment_id,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        features=features,
        feature_hash=semantic_feature_hash(features),
        evidence_refs=list(evidence_refs),
        captured_at=captured_at or utc_now(),
    )


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 1.0
    return len(left_set & right_set) / len(union)


def _screenshot_similarity(left: str | None, right: str | None) -> float | None:
    if left is None or right is None:
        return None
    left = left.casefold()
    right = right.casefold()
    if len(left) == len(right) and re.fullmatch(r"[0-9a-f]+", left + right):
        differing_bits = sum(
            (int(left[index], 16) ^ int(right[index], 16)).bit_count()
            for index in range(len(left))
        )
        return 1 - differing_bits / (len(left) * 4)
    return float(left == right)


def _effective_critical(features: StateObservationFeaturesV1) -> dict[str, str]:
    payload = {
        _normalize_token(key): _normalize_token(value)
        for key, value in features.critical_features.items()
    }
    volatile = {_normalize_token(item) for item in features.volatile_tokens}
    payload["__selected_objects__"] = "|".join(
        _stable_tokens(features.selected_object_tokens, volatile)
    ) or "<none>"
    payload["__overlays__"] = "|".join(
        _stable_tokens(features.overlay_tokens, volatile)
    ) or "<none>"
    return payload


def critical_feature_conflicts(
    left: StateObservationFeaturesV1,
    right: StateObservationFeaturesV1,
) -> tuple[str, ...]:
    left_features = _effective_critical(left)
    right_features = _effective_critical(right)
    return tuple(
        sorted(
            key
            for key in left_features.keys() & right_features.keys()
            if left_features[key] != right_features[key]
        )
    )


def state_observation_similarity(
    left: StateObservationFeaturesV1,
    right: StateObservationFeaturesV1,
) -> tuple[float, tuple[str, ...]]:
    conflicts = critical_feature_conflicts(left, right)
    if conflicts:
        return 0.0, conflicts
    left_payload = stable_feature_payload(left)
    right_payload = stable_feature_payload(right)
    weighted_components: list[tuple[float, float]] = [
        (
            0.25,
            _jaccard(
                left_payload["ui_structure_tokens"],
                right_payload["ui_structure_tokens"],
            ),
        ),
        (
            0.15,
            _jaccard(left_payload["ui_text_tokens"], right_payload["ui_text_tokens"]),
        ),
        (
            0.20,
            _jaccard(left_payload["runtime_tokens"], right_payload["runtime_tokens"]),
        ),
        (
            0.15,
            _jaccard(
                left_payload["selected_object_tokens"],
                right_payload["selected_object_tokens"],
            ),
        ),
        (
            0.15,
            _jaccard(left_payload["overlay_tokens"], right_payload["overlay_tokens"]),
        ),
        (
            0.05,
            _jaccard(
                (
                    f"{key}={value}"
                    for key, value in left_payload["region_fingerprints"].items()
                ),
                (
                    f"{key}={value}"
                    for key, value in right_payload["region_fingerprints"].items()
                ),
            ),
        ),
    ]
    screenshot_score = _screenshot_similarity(
        left.screenshot_fingerprint,
        right.screenshot_fingerprint,
    )
    if screenshot_score is not None:
        weighted_components.append((0.05, screenshot_score))
    total_weight = sum(weight for weight, _score in weighted_components)
    return (
        sum(weight * score for weight, score in weighted_components) / total_weight,
        (),
    )


def _unique_evidence(
    references: Iterable[EvidenceReferenceV1],
) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for reference in references:
        key = reference.model_dump_json(by_alias=True)
        unique.setdefault(key, reference)
    return list(unique.values())


class SemanticStateRecognizer:
    def __init__(
        self,
        store: AIPlayerStore,
        *,
        match_threshold: float = 0.90,
        ambiguity_margin: float = 0.03,
        prototype_limit_per_state: int = 20,
    ) -> None:
        if not 0 < match_threshold <= 1:
            raise ValueError("match_threshold must be in (0, 1]")
        if not 0 <= ambiguity_margin < 1:
            raise ValueError("ambiguity_margin must be in [0, 1)")
        if prototype_limit_per_state < 1:
            raise ValueError("prototype_limit_per_state must be positive")
        self.store = store
        self.match_threshold = match_threshold
        self.ambiguity_margin = ambiguity_margin
        self.prototype_limit_per_state = prototype_limit_per_state

    def recognize(self, observation: StateObservationV1) -> StateRecognitionDecisionV1:
        expected_hash = semantic_feature_hash(observation.features)
        if observation.feature_hash != expected_hash:
            raise ValueError("state observation feature hash does not match its stable features")
        self.store.append_state_observation(observation)
        current_assignment = self.store.get_current_state_assignment(
            observation.environment_id,
            observation.id,
        )
        if current_assignment is not None:
            return StateRecognitionDecisionV1(
                environment_id=observation.environment_id,
                observation_id=observation.id,
                state_id=current_assignment.state_id,
                assignment_id=current_assignment.id,
                disposition="recognized_existing",
                confidence=current_assignment.confidence,
            )

        states = self.store.list_semantic_states(
            observation.environment_id,
            statuses=("candidate", "accepted"),
        )
        ranked = self._rank_states(observation, states)
        exact = [state for state in states if observation.feature_hash in state.observation_feature_hashes]
        if len(exact) == 1:
            return self._assign_existing(
                observation,
                exact[0],
                method="exact_fingerprint",
                confidence=1.0,
                disposition="recognized_existing",
                ranked=ranked,
                reason="稳定特征指纹与已有状态完全一致",
            )

        best = ranked[0] if ranked else None
        runner_up = ranked[1] if len(ranked) > 1 else None
        if (
            best is not None
            and not best.critical_conflicts
            and best.score >= self.match_threshold
            and (runner_up is None or best.score - runner_up.score >= self.ambiguity_margin)
        ):
            state = next(state for state in states if state.id == best.state_id)
            return self._assign_existing(
                observation,
                state,
                method="nearest_prototype",
                confidence=best.score,
                disposition="recognized_existing",
                ranked=ranked,
                reason="多源稳定特征与已有状态原型达到匹配阈值",
            )

        ambiguous = best is not None and best.score >= self.match_threshold
        return self._create_candidate(observation, ranked, ambiguous=ambiguous)

    def _rank_states(
        self,
        observation: StateObservationV1,
        states: Sequence[SemanticStateV1],
    ) -> list[StateMatchV1]:
        current_assignments = self.store.list_state_assignments(
            observation.environment_id,
            latest_only=True,
        )
        observations = {
            item.id: item
            for item in self.store.list_state_observations(observation.environment_id)
        }
        prototypes: dict[str, list[StateObservationV1]] = {}
        for assignment in current_assignments:
            prototype = observations.get(assignment.observation_id)
            if prototype is not None:
                prototypes.setdefault(assignment.state_id, []).append(prototype)
        ranked: list[StateMatchV1] = []
        for state in states:
            scores: list[tuple[float, tuple[str, ...]]] = []
            for prototype in prototypes.get(state.id, [])[-self.prototype_limit_per_state :]:
                scores.append(
                    state_observation_similarity(observation.features, prototype.features)
                )
            if scores:
                score, conflicts = max(scores, key=lambda item: item[0])
            elif observation.feature_hash in state.observation_feature_hashes:
                score, conflicts = 1.0, ()
            else:
                score, conflicts = 0.0, ()
            ranked.append(
                StateMatchV1(
                    state_id=state.id,
                    score=round(score, 6),
                    critical_conflicts=list(conflicts),
                )
            )
        return sorted(ranked, key=lambda item: (-item.score, item.state_id))[:10]

    def _assign_existing(
        self,
        observation: StateObservationV1,
        state: SemanticStateV1,
        *,
        method: Literal["exact_fingerprint", "nearest_prototype"],
        confidence: float,
        disposition: Literal["recognized_existing"],
        ranked: Sequence[StateMatchV1],
        reason: str,
    ) -> StateRecognitionDecisionV1:
        if observation.feature_hash not in state.observation_feature_hashes:
            payload = state.model_dump()
            payload.update(
                {
                    "version": state.version + 1,
                    "observation_feature_hashes": [
                        *state.observation_feature_hashes,
                        observation.feature_hash,
                    ],
                    "evidence_refs": _unique_evidence(
                        [*state.evidence_refs, *observation.evidence_refs]
                    ),
                    "supersedes_id": state.id,
                    "created_at": utc_now(),
                }
            )
            state = self.store.put_semantic_state(SemanticStateV1.model_validate(payload))
        assignment = StateAssignmentV1(
            id=f"assignment.{observation.id}.v1",
            environment_id=observation.environment_id,
            observation_id=observation.id,
            state_id=state.id,
            method=method,
            confidence=confidence,
            reasons=[reason],
            evidence_refs=observation.evidence_refs,
        )
        self.store.append_state_assignment(assignment)
        return StateRecognitionDecisionV1(
            environment_id=observation.environment_id,
            observation_id=observation.id,
            state_id=state.id,
            assignment_id=assignment.id,
            disposition=disposition,
            confidence=confidence,
            ranked_matches=list(ranked),
        )

    def _create_candidate(
        self,
        observation: StateObservationV1,
        ranked: Sequence[StateMatchV1],
        *,
        ambiguous: bool,
    ) -> StateRecognitionDecisionV1:
        base_id = f"state.auto.{observation.feature_hash[:16]}"
        state_id = base_id
        counter = 1
        while self.store.get_semantic_state(observation.environment_id, state_id) is not None:
            counter += 1
            state_id = f"{base_id}.{counter}"
        tags = ["auto-discovered"]
        if ambiguous:
            tags.append("recognition-ambiguous")
        state = SemanticStateV1(
            id=state_id,
            environment_id=observation.environment_id,
            title=f"待审状态 {observation.feature_hash[:8]}",
            description="由多源观察建立，等待独立语义命名与边界审定。",
            semantic_fingerprint=observation.feature_hash,
            observation_feature_hashes=[observation.feature_hash],
            tags=tags,
            status="candidate",
            evidence_refs=observation.evidence_refs,
        )
        self.store.put_semantic_state(state)
        confidence = ranked[0].score if ranked else 1.0
        assignment = StateAssignmentV1(
            id=f"assignment.{observation.id}.v1",
            environment_id=observation.environment_id,
            observation_id=observation.id,
            state_id=state.id,
            method="new_candidate",
            confidence=confidence,
            reasons=[
                "最接近状态的分数不足" if not ambiguous else "多个状态原型过于接近，需要独立审定"
            ],
            evidence_refs=observation.evidence_refs,
        )
        self.store.append_state_assignment(assignment)
        return StateRecognitionDecisionV1(
            environment_id=observation.environment_id,
            observation_id=observation.id,
            state_id=state.id,
            assignment_id=assignment.id,
            disposition="needs_adjudication" if ambiguous else "created_candidate",
            confidence=confidence,
            ranked_matches=list(ranked),
        )

    def merge_states(
        self,
        environment_id: str,
        canonical_state_id: str,
        merged_state_ids: Sequence[str],
        *,
        evidence_refs: Sequence[EvidenceReferenceV1],
    ) -> SemanticStateV1:
        state_ids = list(dict.fromkeys([canonical_state_id, *merged_state_ids]))
        if len(state_ids) < 2:
            raise ValueError("state merge requires at least two distinct states")
        states = [self.store.get_semantic_state(environment_id, state_id) for state_id in state_ids]
        if any(state is None for state in states):
            raise KeyError("state merge references an unknown state")
        concrete_states = [state for state in states if state is not None]
        assignments = self.store.list_state_assignments(environment_id, latest_only=True)
        relevant = [item for item in assignments if item.state_id in state_ids]
        observations = [
            self.store.get_state_observation(environment_id, item.observation_id)
            for item in relevant
        ]
        concrete_observations = [item for item in observations if item is not None]
        for index, left in enumerate(concrete_observations):
            for right in concrete_observations[index + 1 :]:
                conflicts = critical_feature_conflicts(left.features, right.features)
                if conflicts:
                    raise ValueError(
                        "cannot merge states with conflicting critical features: "
                        + ", ".join(conflicts)
                    )
        canonical = concrete_states[0]
        merged_evidence = _unique_evidence(
            [
                *evidence_refs,
                *(reference for state in concrete_states for reference in state.evidence_refs),
            ]
        )
        payload = canonical.model_dump()
        payload.update(
            {
                "version": canonical.version + 1,
                "observation_feature_hashes": list(
                    dict.fromkeys(
                        feature_hash
                        for state in concrete_states
                        for feature_hash in state.observation_feature_hashes
                    )
                ),
                "aliases": list(
                    dict.fromkeys(
                        [
                            *canonical.aliases,
                            *(state.title for state in concrete_states[1:]),
                            *state_ids[1:],
                        ]
                    )
                ),
                "status": "accepted",
                "evidence_refs": merged_evidence,
                "supersedes_id": canonical.id,
                "created_at": utc_now(),
            }
        )
        canonical = self.store.put_semantic_state(SemanticStateV1.model_validate(payload))
        for state in concrete_states[1:]:
            superseded = state.model_dump()
            superseded.update(
                {
                    "version": state.version + 1,
                    "status": "superseded",
                    "tags": list(dict.fromkeys([*state.tags, f"merged-into:{canonical.id}"])),
                    "supersedes_id": state.id,
                    "evidence_refs": merged_evidence,
                    "created_at": utc_now(),
                }
            )
            self.store.put_semantic_state(SemanticStateV1.model_validate(superseded))
        for assignment in relevant:
            if assignment.state_id == canonical.id:
                continue
            revision = StateAssignmentV1(
                id=f"assignment.{assignment.observation_id}.v{assignment.version + 1}",
                version=assignment.version + 1,
                environment_id=environment_id,
                observation_id=assignment.observation_id,
                state_id=canonical.id,
                method="adjudicated_merge",
                confidence=1.0,
                reasons=["独立审定将同义状态归并到统一状态"],
                status="active",
                supersedes_id=assignment.id,
                evidence_refs=_unique_evidence([*assignment.evidence_refs, *evidence_refs]),
            )
            self.store.append_state_assignment(revision)
        return canonical

    def split_state(
        self,
        environment_id: str,
        source_state_id: str,
        partitions: Sequence[StateSplitPartitionV1],
        *,
        evidence_refs: Sequence[EvidenceReferenceV1],
    ) -> tuple[SemanticStateV1, ...]:
        source = self.store.get_semantic_state(environment_id, source_state_id)
        if source is None:
            raise KeyError(f"unknown semantic state: {source_state_id}")
        if len(partitions) < 2:
            raise ValueError("state split requires at least two partitions")
        child_ids = [partition.state_id for partition in partitions]
        if len(child_ids) != len(set(child_ids)) or source_state_id in child_ids:
            raise ValueError("state split child ids must be unique and distinct from the source")
        assignments = [
            item
            for item in self.store.list_state_assignments(environment_id, latest_only=True)
            if item.state_id == source_state_id
        ]
        current_by_observation = {item.observation_id: item for item in assignments}
        partition_ids = [
            observation_id
            for partition in partitions
            for observation_id in partition.observation_ids
        ]
        if len(partition_ids) != len(set(partition_ids)):
            raise ValueError("state split partitions must not reuse observations")
        if set(partition_ids) != set(current_by_observation):
            raise ValueError("state split partitions must cover every current observation exactly once")
        children: list[SemanticStateV1] = []
        for partition in partitions:
            observations = [
                self.store.get_state_observation(environment_id, observation_id)
                for observation_id in partition.observation_ids
            ]
            concrete = [item for item in observations if item is not None]
            if len(concrete) != len(partition.observation_ids):
                raise ValueError("state split references a missing observation")
            for index, left in enumerate(concrete):
                for right in concrete[index + 1 :]:
                    conflicts = critical_feature_conflicts(left.features, right.features)
                    if conflicts:
                        raise ValueError(
                            "state split partition contains conflicting critical features: "
                            + ", ".join(conflicts)
                        )
            child_evidence = _unique_evidence(
                [
                    *evidence_refs,
                    *(reference for item in concrete for reference in item.evidence_refs),
                ]
            )
            feature_hashes = list(dict.fromkeys(item.feature_hash for item in concrete))
            fingerprint = hashlib.sha256("|".join(sorted(feature_hashes)).encode()).hexdigest()
            child = SemanticStateV1(
                id=partition.state_id,
                environment_id=environment_id,
                title=partition.title,
                description=partition.description,
                semantic_fingerprint=fingerprint,
                observation_feature_hashes=feature_hashes,
                tags=list(partition.tags),
                status="candidate",
                supersedes_id=source.id,
                evidence_refs=child_evidence,
            )
            self.store.put_semantic_state(child)
            children.append(child)
            for observation in concrete:
                current = current_by_observation[observation.id]
                revision = StateAssignmentV1(
                    id=f"assignment.{observation.id}.v{current.version + 1}",
                    version=current.version + 1,
                    environment_id=environment_id,
                    observation_id=observation.id,
                    state_id=child.id,
                    method="adjudicated_split",
                    confidence=1.0,
                    reasons=["独立审定将混合状态拆分为具有操作差异的状态"],
                    status="active",
                    supersedes_id=current.id,
                    evidence_refs=_unique_evidence([*current.evidence_refs, *evidence_refs]),
                )
                self.store.append_state_assignment(revision)
        superseded = source.model_dump()
        superseded.update(
            {
                "version": source.version + 1,
                "status": "superseded",
                "tags": list(
                    dict.fromkeys(
                        [*source.tags, *(f"split-into:{child.id}" for child in children)]
                    )
                ),
                "supersedes_id": source.id,
                "evidence_refs": _unique_evidence([*source.evidence_refs, *evidence_refs]),
                "created_at": utc_now(),
            }
        )
        self.store.put_semantic_state(SemanticStateV1.model_validate(superseded))
        return tuple(children)


def load_observation(path: Path) -> StateObservationV1:
    return StateObservationV1.model_validate_json(path.read_text(encoding="utf-8"))