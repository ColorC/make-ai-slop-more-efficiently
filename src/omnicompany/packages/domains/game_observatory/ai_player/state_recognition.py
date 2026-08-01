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
    TransitionEdgeV1,
)
from .store import AIPlayerStore


_DEFAULT_VOLATILE_PREFIXES = (
    "animation-frame:",
    "countdown:",
    "fps:",
    "server-time:",
    "timer:",
)

_UNITY_CANVAS_MARKERS = (
    "unitysurfaceview",
    "com.unity3d.player",
)
_UNITY_CANVAS_GENERIC_TEXT_TOKENS = {
    "content-desc:game view",
}


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
    payload: dict[str, object] = {
        "screenshot_fingerprint": (
            _normalize_token(features.screenshot_fingerprint)
            if features.screenshot_fingerprint
            else None
        ),
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
    # Keep the v1 hash of legacy observations unchanged. Enriched semantic
    # surface fields participate only when a producer actually supplies them.
    for field_name in (
        "page_identity_tokens",
        "dynamic_field_names",
        "interaction_roles",
        "safe_exit_tokens",
        "risk_boundary_tokens",
    ):
        values = _stable_tokens(getattr(features, field_name), volatile)
        if values:
            payload[field_name] = values
    return payload


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
    created_at: str | None = None,
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
        created_at=created_at or utc_now(),
    )


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 1.0
    return len(left_set & right_set) / len(union)


def _screenshot_similarity(left: str | None, right: str | None) -> float | None:
    if left is None and right is None:
        return None
    if left is None or right is None:
        return 0.0
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


def _is_unity_canvas_only(features: StateObservationFeaturesV1) -> bool:
    """Detect an Android tree that exposes only Unity's canvas host.

    Such a tree is identical across nearly every in-game screen. It remains
    useful as a capture-profile guard, but it must not make two visually
    different screens look semantically similar. Any selected object, overlay,
    non-generic text, or additional critical signal disables this shortcut.
    """

    volatile = {_normalize_token(item) for item in features.volatile_tokens}
    structure = _stable_tokens(features.ui_structure_tokens, volatile)
    if not structure or not any(
        marker in token for token in structure for marker in _UNITY_CANVAS_MARKERS
    ):
        return False
    if _stable_tokens(features.selected_object_tokens, volatile):
        return False
    if _stable_tokens(features.overlay_tokens, volatile):
        return False
    if any(
        _stable_tokens(values, volatile)
        for values in (
            features.page_identity_tokens,
            features.dynamic_field_names,
            features.interaction_roles,
            features.safe_exit_tokens,
            features.risk_boundary_tokens,
        )
    ):
        return False
    text = set(_stable_tokens(features.ui_text_tokens, volatile))
    if text - _UNITY_CANVAS_GENERIC_TEXT_TOKENS:
        return False
    critical_keys = {_normalize_token(key) for key in features.critical_features}
    return critical_keys <= {"ui-package", "ui-root-class"}


def _is_unadjudicated_ambiguous_candidate(state: SemanticStateV1) -> bool:
    return state.status == "candidate" and "recognition-ambiguous" in state.tags


def critical_feature_conflicts(
    left: StateObservationFeaturesV1,
    right: StateObservationFeaturesV1,
) -> tuple[str, ...]:
    left_features = _effective_critical(left)
    right_features = _effective_critical(right)
    conflicts = {
        key
        for key in left_features.keys() | right_features.keys()
        if left_features.get(key) != right_features.get(key)
    }
    left_volatile = {_normalize_token(item) for item in left.volatile_tokens}
    right_volatile = {_normalize_token(item) for item in right.volatile_tokens}
    for conflict_key, field_name in (
        ("__page_identity__", "page_identity_tokens"),
        ("__safe_exits__", "safe_exit_tokens"),
        ("__risk_boundaries__", "risk_boundary_tokens"),
    ):
        left_values = _stable_tokens(getattr(left, field_name), left_volatile)
        right_values = _stable_tokens(getattr(right, field_name), right_volatile)
        # Missing enriched semantics means "not observed", not "absent". This
        # preserves recognition against historical observations while making
        # conflicting explicit profiles a hard boundary.
        if left_values and right_values and left_values != right_values:
            conflicts.add(conflict_key)
    return tuple(sorted(conflicts))


def state_observation_similarity(
    left: StateObservationFeaturesV1,
    right: StateObservationFeaturesV1,
) -> tuple[float, tuple[str, ...]]:
    conflicts = critical_feature_conflicts(left, right)
    if conflicts:
        return 0.0, conflicts
    left_payload = stable_feature_payload(left)
    right_payload = stable_feature_payload(right)
    weighted_components: list[tuple[float, float]] = []
    canvas_only_pair = _is_unity_canvas_only(left) and _is_unity_canvas_only(right)

    def append_jaccard_component(
        weight: float,
        left_values: Iterable[str],
        right_values: Iterable[str],
    ) -> None:
        left_items = tuple(left_values)
        right_items = tuple(right_values)
        if not left_items and not right_items:
            return
        weighted_components.append((weight, _jaccard(left_items, right_items)))

    if not canvas_only_pair:
        append_jaccard_component(
            0.25,
            left_payload["ui_structure_tokens"],
            right_payload["ui_structure_tokens"],
        )
        append_jaccard_component(
            0.15,
            left_payload["ui_text_tokens"],
            right_payload["ui_text_tokens"],
        )
        append_jaccard_component(
            0.20,
            left_payload["runtime_tokens"],
            right_payload["runtime_tokens"],
        )
    append_jaccard_component(
        0.15,
        left_payload["selected_object_tokens"],
        right_payload["selected_object_tokens"],
    )
    append_jaccard_component(
        0.15,
        left_payload["overlay_tokens"],
        right_payload["overlay_tokens"],
    )
    for weight, field_name in (
        (0.30, "page_identity_tokens"),
        (0.10, "dynamic_field_names"),
        (0.15, "interaction_roles"),
        (0.10, "safe_exit_tokens"),
        (0.20, "risk_boundary_tokens"),
    ):
        append_jaccard_component(
            weight,
            left_payload.get(field_name, ()),
            right_payload.get(field_name, ()),
        )
    append_jaccard_component(
        0.05,
        (f"{key}={value}" for key, value in left_payload["region_fingerprints"].items()),
        (f"{key}={value}" for key, value in right_payload["region_fingerprints"].items()),
    )
    screenshot_score = _screenshot_similarity(
        left.screenshot_fingerprint,
        right.screenshot_fingerprint,
    )
    if screenshot_score is not None:
        weighted_components.append((0.05, screenshot_score))
    total_weight = sum(weight for weight, _score in weighted_components)
    if total_weight == 0:
        return 0.0, ()
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
        created_at: str | None = None,
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
        self.created_at = created_at

    def _now(self) -> str:
        return self.created_at or utc_now()

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
            current_state = self.store.get_semantic_state(
                observation.environment_id,
                current_assignment.state_id,
            )
            remains_quarantined = (
                current_state is not None
                and _is_unadjudicated_ambiguous_candidate(current_state)
                and current_assignment.method
                not in {"source_state_guard", "expected_state_guard"}
            )
            return StateRecognitionDecisionV1(
                environment_id=observation.environment_id,
                observation_id=observation.id,
                state_id=current_assignment.state_id,
                assignment_id=current_assignment.id,
                disposition=(
                    "needs_adjudication" if remains_quarantined else "recognized_existing"
                ),
                confidence=current_assignment.confidence,
                created_at=self._now(),
            )

        exact = self.store.list_active_semantic_states_by_feature_hash(
            observation.environment_id,
            observation.feature_hash,
        )
        exact = [
            state
            for state in exact
            if not _is_unadjudicated_ambiguous_candidate(state)
        ]
        if len(exact) == 1:
            exact_match = StateMatchV1(
                state_id=exact[0].id,
                score=1.0,
                critical_conflicts=[],
            )
            return self._assign_existing(
                observation,
                exact[0],
                method="exact_fingerprint",
                confidence=1.0,
                disposition="recognized_existing",
                ranked=[exact_match],
                reason="稳定特征指纹与已有状态完全一致",
            )

        all_states = self.store.list_semantic_states(
            observation.environment_id,
            statuses=("candidate", "accepted"),
        )
        states = [
            state
            for state in all_states
            if not _is_unadjudicated_ambiguous_candidate(state)
        ]
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

        quarantined_exact = [
            state
            for state in all_states
            if _is_unadjudicated_ambiguous_candidate(state)
            and observation.feature_hash in state.observation_feature_hashes
        ]
        if quarantined_exact and not exact:
            return self._retain_ambiguous_candidate(
                observation,
                min(quarantined_exact, key=lambda state: state.id),
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

    def _retain_ambiguous_candidate(
        self,
        observation: StateObservationV1,
        state: SemanticStateV1,
    ) -> StateRecognitionDecisionV1:
        """Keep an exact ambiguous observation quarantined pending adjudication.

        The state is deliberately not versioned and the new assignment is not a
        recognized-existing success. Because quarantined states are excluded
        from normal prototype ranking, repeated observations cannot bootstrap an
        unresolved candidate into a trusted prototype.
        """

        assignment = StateAssignmentV1(
            id=f"assignment.{observation.id}.v1",
            environment_id=observation.environment_id,
            observation_id=observation.id,
            state_id=state.id,
            method="new_candidate",
            confidence=0.0,
            reasons=["相同指纹仍属于未裁决的歧义候选，继续隔离等待独立裁决"],
            evidence_refs=observation.evidence_refs,
            created_at=self._now(),
        )
        self.store.append_state_assignment(assignment)
        return StateRecognitionDecisionV1(
            environment_id=observation.environment_id,
            observation_id=observation.id,
            state_id=state.id,
            assignment_id=assignment.id,
            disposition="needs_adjudication",
            confidence=0.0,
            ranked_matches=[],
            created_at=self._now(),
        )

    def recognize_from_source_state_guard(
        self,
        observation: StateObservationV1,
        state_id: str,
    ) -> StateRecognitionDecisionV1:
        """Bind a pre-action observation to its already verified source state.

        The live executor has already hash-checked the source artifact and confirmed
        that the device still showed it immediately before the action.  Re-running
        nearest-neighbour recognition here can fragment one dynamic interface into
        many states, so this stronger temporal identity wins for the before endpoint.
        """

        return self.recognize_from_verified_state_guard(
            observation,
            state_id,
            method="source_state_guard",
        )

    def recognize_from_verified_state_guard(
        self,
        observation: StateObservationV1,
        state_id: str,
        *,
        method: Literal["source_state_guard", "expected_state_guard"],
    ) -> StateRecognitionDecisionV1:
        """Bind an observation when an execution-time visual guard proved identity."""

        expected_hash = semantic_feature_hash(observation.features)
        if observation.feature_hash != expected_hash:
            raise ValueError("state observation feature hash does not match its stable features")
        self.store.append_state_observation(observation)
        current_assignment = self.store.get_current_state_assignment(
            observation.environment_id,
            observation.id,
        )
        if current_assignment is not None:
            if current_assignment.state_id != state_id and method != "expected_state_guard":
                raise ValueError(
                    "source-state guard conflicts with the current observation assignment: "
                    f"{observation.id}"
                )
            if current_assignment.state_id == state_id:
                return StateRecognitionDecisionV1(
                    environment_id=observation.environment_id,
                    observation_id=observation.id,
                    state_id=current_assignment.state_id,
                    assignment_id=current_assignment.id,
                    disposition="recognized_existing",
                    confidence=current_assignment.confidence,
                    created_at=self._now(),
                )

        state = self.store.get_semantic_state(observation.environment_id, state_id)
        if state is None or state.status not in {"candidate", "accepted"}:
            raise ValueError(f"source-state guard names an unavailable semantic state: {state_id}")
        # A verified source/terminal guard is stronger than semantic nearest-
        # neighbour ranking.  Rebuilding every historical prototype here adds no
        # safety signal: the execution-time frame comparison already proved this
        # exact state, and the named state was checked above.  Keep one explicit
        # perfect match in the decision receipt instead of scanning the graph.
        ranked = [StateMatchV1(state_id=state.id, score=1.0)]
        return self._assign_existing(
            observation,
            state,
            method=method,
            confidence=1.0,
            disposition="recognized_existing",
            ranked=ranked,
            reason=(
                "动作执行前已通过哈希锁定的来源截图和设备画面复核，沿用该来源语义状态。"
                if method == "source_state_guard"
                else "确定性技能声明的预期终态已通过截图距离复核，沿用该终态语义状态。"
            ),
            supersedes_assignment=current_assignment,
        )

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
        method: Literal[
            "exact_fingerprint",
            "nearest_prototype",
            "source_state_guard",
            "expected_state_guard",
        ],
        confidence: float,
        disposition: Literal["recognized_existing"],
        ranked: Sequence[StateMatchV1],
        reason: str,
        supersedes_assignment: StateAssignmentV1 | None = None,
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
                    "created_at": self._now(),
                }
            )
            state = self.store.put_semantic_state(SemanticStateV1.model_validate(payload))
        assignment = StateAssignmentV1(
            id=(
                f"assignment.{observation.id}.v{supersedes_assignment.version + 1}"
                if supersedes_assignment is not None
                else f"assignment.{observation.id}.v1"
            ),
            version=(supersedes_assignment.version + 1 if supersedes_assignment is not None else 1),
            environment_id=observation.environment_id,
            observation_id=observation.id,
            state_id=state.id,
            method=method,
            confidence=confidence,
            reasons=[reason],
            supersedes_id=(
                supersedes_assignment.id if supersedes_assignment is not None else None
            ),
            evidence_refs=observation.evidence_refs,
            created_at=self._now(),
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
            created_at=self._now(),
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
            created_at=self._now(),
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
            created_at=self._now(),
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
            created_at=self._now(),
        )

    def merge_states(
        self,
        environment_id: str,
        canonical_state_id: str,
        merged_state_ids: Sequence[str],
        *,
        evidence_refs: Sequence[EvidenceReferenceV1],
    ) -> SemanticStateV1:
        raise ValueError("state merge requires signed state adjudication")

        # Kept below as the historical algorithm reference while signed review
        # owns the only production mutation path.
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
        for edge in self.store.list_transition_edges(environment_id, latest_only=True):
            if edge.from_state_id not in state_ids and edge.to_state_id not in state_ids:
                continue
            from_state_id = (
                canonical.id if edge.from_state_id in state_ids else edge.from_state_id
            )
            to_state_id = canonical.id if edge.to_state_id in state_ids else edge.to_state_id
            outcome = edge.outcome
            observed_change = edge.observed_change
            if (
                from_state_id == to_state_id
                and outcome in {"verified_transition", "verified_state_change"}
            ):
                outcome = "verified_no_change"
                observed_change = (
                    f"{observed_change}；语义审定归并后起点与终点为同一状态"
                )
            edge_payload = edge.model_dump()
            edge_payload.update(
                {
                    "version": edge.version + 1,
                    "from_state_id": from_state_id,
                    "to_state_id": to_state_id,
                    "outcome": outcome,
                    "observed_change": observed_change,
                    "evidence_refs": _unique_evidence(
                        [*edge.evidence_refs, *evidence_refs]
                    ),
                    "created_at": utc_now(),
                }
            )
            self.store.put_transition_edge(TransitionEdgeV1.model_validate(edge_payload))
        return canonical

    def split_state(
        self,
        environment_id: str,
        source_state_id: str,
        partitions: Sequence[StateSplitPartitionV1],
        *,
        evidence_refs: Sequence[EvidenceReferenceV1],
    ) -> tuple[SemanticStateV1, ...]:
        raise ValueError("state split requires signed state adjudication")

        # Kept below as the historical algorithm reference while signed review
        # owns the only production mutation path.
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
