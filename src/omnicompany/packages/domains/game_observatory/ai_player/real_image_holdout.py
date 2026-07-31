"""Build and evaluate evidence-backed candidate real-image holdout corpora.

This module deliberately keeps three files separate:

* the immutable evidence fixture;
* independently reviewable semantic labels;
* recognizer predictions and diagnostic scores.

The builder only reads the canonical Observatory SQLite database through a read-only
connection.  Its generated labels are explicitly unreviewed candidates and can never
make the Stage-1 G-02 gate pass.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..models import ArtifactRef, EvidenceRun, EvidenceStep, LIFECYCLE_ACTION_TYPES
from ..store import default_observatory_root
from .contracts import StateObservationFeaturesV1
from .state_recognition import state_observation_similarity


FIXTURE_SCHEMA = "ai-player-real-image-holdout-fixture.v2"
LABEL_SCHEMA = "ai-player-real-image-holdout-labels.v2"
FIXTURE_SCHEMA_V3 = "ai-player-real-image-holdout-fixture.v3"
LABEL_SCHEMA_V3 = "ai-player-real-image-holdout-labels.v3"
RESULT_SCHEMA = "ai-player-real-image-holdout-result.v2"
ATTESTATION_SCHEMA = "ai-player-real-image-holdout-attestation.v1"
GENERATOR_VERSION = 2
GENERATOR_VERSION_V3 = 3
AFK_ADJUDICATION_SCHEMA = "game-observatory.ai-player.afk-v4-adjudication.v1"
AFK_ADJUDICATION_ID = "afk_hero_growth_v1_candidate_v4_adjudication"
AFK_ADJUDICATED_STATE_ID = "screen.afk.world.source-tracking.savanna"
AFK_ADJUDICATED_CONFLICT_COUNT = 6
FIXTURE_PURPOSE = "g02-real-image-holdout-candidate"
CANDIDATE_LABEL_PURPOSE = "candidate-labels-not-ground-truth"
SIGNED_LABEL_PURPOSE = "independent-holdout-ground-truth"
RESULT_PURPOSE = "g02-real-image-holdout-evaluation"
SUPPORTED_GAMES = ("afk-journey", "sanguo-mouding-tianxia")
POLICY = {
    "id": "semantic-state-recognizer-default.v1",
    "match_threshold": 0.90,
    "ambiguity_margin": 0.03,
    "minimum_accuracy": 0.97,
    "maximum_critical_false_merge_count": 0,
    "maximum_overall_false_merge_rate": 0.03,
    "maximum_over_split_rate": 0.03,
    "near_duplicate_dhash_distance": 6,
    "near_duplicate_pixel_distance": 0.015,
}
POLICY_SHA256 = hashlib.sha256(
    json.dumps(POLICY, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
).hexdigest()
MINIMUM_HOLDOUT_REQUIREMENTS = {
    "afk-journey": {"samples": 20, "unique_states": 10, "critical_states": 1},
    "sanguo-mouding-tianxia": {
        "samples": 20,
        "unique_states": 10,
        "critical_states": 1,
    },
}

# Registration is intentionally empty until an independent reviewer public key is
# approved in code review. Tests inject an ephemeral registered key; production fails
# closed for every unregistered id and never trusts a repository-file hash alone.
TRUSTED_REVIEWER_REGISTRY: dict[str, dict[str, str]] = {}


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{context} keys mismatch; missing={missing}, extra={extra}")


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _parse_aware_datetime(value: Any, context: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{context} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
    return parsed


class _ReadOnlyEvidence:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        self.root = self.db_path.parent.resolve()
        self.artifact_root = (self.root / "artifacts").resolve()
        self.repository_root = self.root.parent.parent.parent.resolve()
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")
        self.connection.execute("BEGIN")

    def close(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def __enter__(self) -> _ReadOnlyEvidence:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def list_runs(self, cutoff_started_at: str) -> list[EvidenceRun]:
        rows = self.connection.execute(
            """SELECT body_json FROM evidence_runs
               WHERE started_at <= ? AND ended_at IS NOT NULL
               ORDER BY started_at,id""",
            (cutoff_started_at,),
        ).fetchall()
        return [EvidenceRun.model_validate_json(row["body_json"]) for row in rows]

    def get_run(self, run_id: str) -> EvidenceRun | None:
        row = self.connection.execute(
            "SELECT body_json FROM evidence_runs WHERE id=?", (run_id,)
        ).fetchone()
        return EvidenceRun.model_validate_json(row["body_json"]) if row else None

    def get_step(self, step_id: str) -> EvidenceStep | None:
        row = self.connection.execute(
            "SELECT body_json FROM evidence_steps WHERE id=?", (step_id,)
        ).fetchone()
        return EvidenceStep.model_validate_json(row["body_json"]) if row else None

    def get_artifact(self, artifact_id: str) -> ArtifactRef | None:
        row = self.connection.execute(
            "SELECT body_json FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        return ArtifactRef.model_validate_json(row["body_json"]) if row else None

    def verified_artifact(self, artifact_id: str, *, expected_kind: str) -> ArtifactRef:
        artifact = self.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"unknown canonical artifact: {artifact_id}")
        if artifact.kind != expected_kind:
            raise ValueError(
                f"artifact {artifact_id} kind is {artifact.kind}, expected {expected_kind}"
            )
        path = Path(artifact.path).resolve()
        if not _inside(path, self.artifact_root):
            raise ValueError(f"artifact path escapes canonical root: {artifact_id}")
        if not path.is_file():
            raise ValueError(f"artifact path does not exist: {artifact_id}")
        if _sha256_file(path) != artifact.sha256:
            raise ValueError(f"artifact file hash mismatch: {artifact_id}")
        return artifact


def _artifact_binding(artifact: ArtifactRef) -> dict[str, Any]:
    return {
        "artifact_id": artifact.id,
        "kind": artifact.kind,
        "canonical_path": str(Path(artifact.path).resolve()),
        "sha256": artifact.sha256,
        "capture_run_id": artifact.run_id,
        "media_type": artifact.media_type,
    }


def _sample(
    reader: _ReadOnlyEvidence,
    *,
    game_id: str,
    run: EvidenceRun,
    step: EvidenceStep,
    role: str,
    screenshot_id: str,
    ui_tree_id: str | None,
    label_hint: str | None,
    label_origin: str | None,
) -> dict[str, Any]:
    screenshot = reader.verified_artifact(screenshot_id, expected_kind="screenshot")
    ui_tree = (
        reader.verified_artifact(ui_tree_id, expected_kind="ui_tree")
        if ui_tree_id
        else None
    )
    return {
        "id": f"sample.{game_id}.{screenshot.id}",
        "game_id": game_id,
        "viewport_width": run.viewport_width,
        "viewport_height": run.viewport_height,
        "screenshot": _artifact_binding(screenshot),
        "ui_tree": _artifact_binding(ui_tree) if ui_tree else None,
        "evidence_run_id": run.id,
        "evidence_step_id": step.id,
        "evidence_role": role,
        "build_scope_id": run.build_scope_id,
        "evidence_scope_id": run.scope_id,
        "source_group_id": run.id,
        "source_family_id": run.id,
        "candidate_label_hint": label_hint,
        "candidate_label_origin": label_origin,
    }


def _run_has_holdout_ineligible_lifecycle_step(
    reader: _ReadOnlyEvidence,
    run: EvidenceRun,
) -> bool:
    for step_id in run.step_ids:
        step = reader.get_step(step_id)
        if step is None:
            raise ValueError(f"evidence run has a dead step reference: {run.id}:{step_id}")
        if step.action.type == "force_stop" or (
            step.action.type in LIFECYCLE_ACTION_TYPES
            and step.metadata.get("real_image_holdout_eligible") is False
        ):
            return True
    return False


def _deduplicate_samples(samples: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_artifacts: set[str] = set()
    seen_hashes: set[str] = set()
    duplicate_count = 0
    for sample in sorted(samples, key=lambda item: item["id"]):
        artifact = sample["screenshot"]
        if (
            sample["id"] in seen_ids
            or artifact["artifact_id"] in seen_artifacts
            or artifact["sha256"] in seen_hashes
        ):
            duplicate_count += 1
            continue
        seen_ids.add(sample["id"])
        seen_artifacts.add(artifact["artifact_id"])
        seen_hashes.add(artifact["sha256"])
        kept.append(sample)
    return kept, duplicate_count


def _hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


@lru_cache(maxsize=4096)
def _pixel_signature(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot decode screenshot: {path}")
    return cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)


def _pixel_distance(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.mean(np.abs(left - right)) / 255.0)


def _near_duplicate(
    left_hash: str,
    right_hash: str,
    left_pixels: np.ndarray,
    right_pixels: np.ndarray,
) -> bool:
    return bool(
        _hamming_distance(left_hash, right_hash)
        <= POLICY["near_duplicate_dhash_distance"]
        or _pixel_distance(left_pixels, right_pixels)
        <= POLICY["near_duplicate_pixel_distance"]
    )


def _source_family_assignments(
    samples: Sequence[Mapping[str, Any]],
    reader: _ReadOnlyEvidence,
) -> dict[str, str]:
    """Freeze leakage families across run, session, video, and adjacent-image relations."""

    ordered_samples = sorted(samples, key=lambda item: str(item["id"]))
    parent = {str(item["id"]): str(item["id"]) for item in ordered_samples}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        parent[second] = first

    relation_members: dict[str, list[str]] = defaultdict(list)
    run_by_id: dict[str, EvidenceRun] = {}
    step_by_id: dict[str, EvidenceStep] = {}
    for sample in ordered_samples:
        sample_id = str(sample["id"])
        run_id = str(sample["evidence_run_id"])
        step_id = str(sample["evidence_step_id"])
        if run_id not in run_by_id:
            run_by_id[run_id] = reader.get_run(run_id)
        if step_id not in step_by_id:
            step_by_id[step_id] = reader.get_step(step_id)
        run = run_by_id[run_id]
        step = step_by_id[step_id]
        if run is None or step is None:
            raise ValueError(f"family source has dead run/step: {sample_id}")
        relation_members[f"run:{run_id}"].append(sample_id)
        for key in (
            "session_id",
            "capture_session_id",
            "recording_session_id",
            "caller_session_id",
        ):
            value = run.environment.get(key)
            if isinstance(value, str) and value.strip():
                relation_members[f"session:{key}:{value}"].append(sample_id)
        if step.video_artifact_id:
            relation_members[f"video:{step.video_artifact_id}"].append(sample_id)
        screenshot = reader.get_artifact(str(sample["screenshot"]["artifact_id"]))
        if screenshot is None:
            raise ValueError(f"family source has dead screenshot: {sample_id}")
        for key in ("session_id", "capture_session_id", "recording_session_id"):
            value = screenshot.metadata.get(key)
            if isinstance(value, str) and value.strip():
                relation_members[f"artifact-session:{key}:{value}"].append(sample_id)
        if step.video_artifact_id:
            video = reader.get_artifact(step.video_artifact_id)
            if video is not None:
                for key in ("session_id", "capture_session_id", "recording_session_id"):
                    value = video.metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        relation_members[f"video-session:{key}:{value}"].append(sample_id)
    for members in relation_members.values():
        for member in members[1:]:
            union(members[0], member)

    hash_cache: dict[str, str] = {}
    pixel_cache: dict[str, np.ndarray] = {}
    for index, left in enumerate(ordered_samples):
        left_id = str(left["id"])
        left_path = Path(str(left["screenshot"]["canonical_path"]))
        if left_id not in hash_cache:
            hash_cache[left_id] = _dhash(left_path)
            pixel_cache[left_id] = _pixel_signature(left_path)
        for right in ordered_samples[index + 1 :]:
            if left["game_id"] != right["game_id"]:
                continue
            if (
                left["viewport_width"] != right["viewport_width"]
                or left["viewport_height"] != right["viewport_height"]
            ):
                continue
            right_id = str(right["id"])
            right_path = Path(str(right["screenshot"]["canonical_path"]))
            if right_id not in hash_cache:
                hash_cache[right_id] = _dhash(right_path)
                pixel_cache[right_id] = _pixel_signature(right_path)
            if _near_duplicate(
                hash_cache[left_id],
                hash_cache[right_id],
                pixel_cache[left_id],
                pixel_cache[right_id],
            ):
                union(left_id, right_id)

    adjacent_runs = sorted(
        run_by_id.values(),
        key=lambda item: (_parse_aware_datetime(item.started_at, "run.started_at"), item.id),
    )
    samples_by_run_role = {
        (str(item["evidence_run_id"]), str(item["evidence_role"])): str(item["id"])
        for item in ordered_samples
    }
    for left_run, right_run in zip(adjacent_runs, adjacent_runs[1:]):
        if left_run.game_id != right_run.game_id or left_run.target_id != right_run.target_id:
            continue
        if left_run.ended_at is None:
            continue
        gap = (
            _parse_aware_datetime(right_run.started_at, "run.started_at")
            - _parse_aware_datetime(left_run.ended_at, "run.ended_at")
        ).total_seconds()
        if not 0 <= gap <= 120:
            continue
        left_id = samples_by_run_role.get((left_run.id, "after"))
        right_id = samples_by_run_role.get((right_run.id, "before"))
        if left_id and right_id and _near_duplicate(
            hash_cache[left_id],
            hash_cache[right_id],
            pixel_cache[left_id],
            pixel_cache[right_id],
        ):
            union(left_id, right_id)

    components: dict[str, list[str]] = defaultdict(list)
    for sample_id in parent:
        components[find(sample_id)].append(sample_id)
    assignments: dict[str, str] = {}
    sample_by_id = {str(item["id"]): item for item in ordered_samples}
    for members in components.values():
        members = sorted(members)
        games = {str(sample_by_id[item]["game_id"]) for item in members}
        if len(games) != 1:
            raise ValueError(f"source family crosses games: {members}")
        game_id = next(iter(games))
        digest = hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest()[:20]
        family_id = f"family.{game_id}.{digest}"
        assignments.update({item: family_id for item in members})
    return assignments


def _partition_afk(
    samples: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    by_label: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for sample in samples:
        label = sample.get("candidate_label_hint")
        if label:
            by_label[label].setdefault(sample["source_family_id"], sample)

    group_partition: dict[str, str] = {}
    for label in sorted(by_label, key=lambda item: (len(by_label[item]), item)):
        group_samples = by_label[label]
        groups = sorted(group_samples)
        if len(groups) < 2:
            continue
        reference_options = [
            group for group in groups if group_partition.get(group) in {None, "reference"}
        ]
        holdout_options = [
            group for group in groups if group_partition.get(group) in {None, "holdout"}
        ]
        pair = next(
            (
                (left, right)
                for left in reference_options
                for right in holdout_options
                if left != right
            ),
            None,
        )
        if pair is None:
            continue
        group_partition[pair[0]] = "reference"
        group_partition[pair[1]] = "holdout"

    for group in sorted({sample["source_family_id"] for sample in samples}):
        if group not in group_partition:
            group_partition[group] = (
                "reference"
                if int(hashlib.sha256(group.encode("utf-8")).hexdigest()[:2], 16) % 2 == 0
                else "holdout"
            )

    reference: list[dict[str, str]] = []
    holdout: list[dict[str, str]] = []
    labels_with_both = {
        label
        for label, grouped in by_label.items()
        if {group_partition[group] for group in grouped} == {"reference", "holdout"}
    }
    for sample in samples:
        label = sample.get("candidate_label_hint")
        if label not in labels_with_both:
            continue
        entry = {
            "sample_id": sample["id"],
            "semantic_state_id": label,
            "label_origin": str(sample["candidate_label_origin"]),
            "label_locator": f"{sample['evidence_run_id']}:{sample['evidence_role']}",
        }
        if group_partition[sample["source_family_id"]] == "reference":
            reference.append(entry)
        else:
            holdout.append(entry)
    return reference, holdout, group_partition


def _partition_sanguo_boundaries(
    runs: Sequence[EvidenceRun],
    samples_by_run_role: Mapping[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    reference: list[dict[str, str]] = []
    holdout: list[dict[str, str]] = []
    group_partition: dict[str, str] = {}
    ordered = sorted(runs, key=lambda item: (item.started_at, item.id))
    for pair_index in range(0, len(ordered) - 1, 2):
        left = ordered[pair_index]
        right = ordered[pair_index + 1]
        left_sample = samples_by_run_role.get((left.id, "after"))
        right_sample = samples_by_run_role.get((right.id, "before"))
        if left_sample is None or right_sample is None:
            continue
        label = f"candidate.nslg.postlogin-boundary.{pair_index // 2 + 1:03d}"
        reference.append(
            {
                "sample_id": left_sample["id"],
                "semantic_state_id": label,
                "label_origin": "chronological-postlogin-boundary-inference",
                "label_locator": f"after:{left.id}",
            }
        )
        holdout.append(
            {
                "sample_id": right_sample["id"],
                "semantic_state_id": label,
                "label_origin": "chronological-postlogin-boundary-inference",
                "label_locator": f"before:{right.id}",
            }
        )
        left_family = str(left_sample["source_family_id"])
        right_family = str(right_sample["source_family_id"])
        if left_family == right_family:
            reference.pop()
            holdout.pop()
            continue
        if group_partition.get(left_family) not in {None, "reference"}:
            reference.pop()
            holdout.pop()
            continue
        if group_partition.get(right_family) not in {None, "holdout"}:
            reference.pop()
            holdout.pop()
            continue
        group_partition[left_family] = "reference"
        group_partition[right_family] = "holdout"
    return reference, holdout, group_partition


def _apply_afk_evidence_adjudication(
    reader: _ReadOnlyEvidence,
    samples: Sequence[dict[str, Any]],
    *,
    adjudication_path: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Replace contradicted route metadata with a detached evidence adjudication.

    The adjudication remains an unsigned AI evidence reading.  It can invalidate an
    older candidate endpoint label, but it can neither create human truth nor unlock
    G-02.  Samples whose corrected state has no disjoint reference family are left in
    the immutable fixture and are explicitly excluded from candidate labels later.
    """

    resolved_path = adjudication_path.resolve()
    if not _inside(resolved_path, reader.repository_root):
        raise ValueError("AFK adjudication source must stay inside the repository")
    if not resolved_path.is_file():
        raise FileNotFoundError(resolved_path)
    actual_sha256 = _sha256_file(resolved_path)
    if actual_sha256 != expected_sha256:
        raise ValueError("AFK adjudication does not match the detached expected hash")
    raw = json.loads(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("AFK adjudication must be an object")
    _require_exact_keys(
        raw,
        {
            "schema",
            "id",
            "benchmark_id",
            "semantic_status",
            "human_truth_status",
            "observed_states",
            "observed_edges",
            "invalid_replays",
        },
        "AFK adjudication",
    )
    if raw["schema"] != AFK_ADJUDICATION_SCHEMA or raw["id"] != AFK_ADJUDICATION_ID:
        raise ValueError("AFK adjudication identity is unsupported")
    if raw["benchmark_id"] != "afk_hero_growth_v1":
        raise ValueError("AFK adjudication benchmark is unsupported")
    if raw["semantic_status"] != "evidence_backed_candidate":
        raise ValueError("AFK adjudication is not evidence-backed candidate material")
    if raw["human_truth_status"] != "not_signed":
        raise ValueError("AFK adjudication must not claim human truth")
    observed_states = raw["observed_states"]
    invalid_replays = raw["invalid_replays"]
    if not isinstance(observed_states, list) or not isinstance(invalid_replays, list):
        raise ValueError("AFK adjudication states and invalid replays must be lists")
    target_states = [
        state
        for state in observed_states
        if isinstance(state, Mapping) and state.get("id") == AFK_ADJUDICATED_STATE_ID
    ]
    if len(target_states) != 1:
        raise ValueError("AFK adjudication must contain exactly one repaired state")
    target_state = target_states[0]
    _require_exact_keys(
        target_state,
        {"id", "name", "visible_facts", "evidence_step_ids", "artifact_ids"},
        "AFK adjudicated state",
    )
    evidence_step_ids = target_state["evidence_step_ids"]
    artifact_ids = target_state["artifact_ids"]
    if not isinstance(evidence_step_ids, list) or not isinstance(artifact_ids, list):
        raise ValueError("AFK adjudicated state evidence must be lists")
    if len(artifact_ids) != AFK_ADJUDICATED_CONFLICT_COUNT or len(set(artifact_ids)) != len(
        artifact_ids
    ):
        raise ValueError(
            f"AFK adjudication must identify {AFK_ADJUDICATED_CONFLICT_COUNT} unique conflicts"
        )
    samples_by_artifact = {
        str(sample["screenshot"]["artifact_id"]): sample
        for sample in samples
        if sample["game_id"] == "afk-journey"
    }
    replay_by_run: dict[str, Mapping[str, Any]] = {}
    for replay in invalid_replays:
        if not isinstance(replay, Mapping):
            raise ValueError("AFK invalid replay must be an object")
        run_id = replay.get("evidence_run_id")
        if isinstance(run_id, str):
            replay_by_run[run_id] = replay

    corrections: list[dict[str, Any]] = []
    for artifact_id in sorted(str(item) for item in artifact_ids):
        sample = samples_by_artifact.get(artifact_id)
        if sample is None:
            raise ValueError(f"AFK adjudication artifact is absent from candidate fixture: {artifact_id}")
        step_id = str(sample["evidence_step_id"])
        run_id = str(sample["evidence_run_id"])
        if step_id not in evidence_step_ids:
            raise ValueError(f"AFK adjudication step does not bind its artifact: {artifact_id}")
        replay = replay_by_run.get(run_id)
        if replay is None:
            raise ValueError(f"AFK adjudication artifact is not from an invalid replay: {artifact_id}")
        if (
            replay.get("replay_status") != "invalid_start_state"
            or replay.get("semantic_goal_status") != "failed_not_reached"
            or replay.get("observed_start_state_id") != AFK_ADJUDICATED_STATE_ID
            or replay.get("observed_end_state_id") != AFK_ADJUDICATED_STATE_ID
            or step_id not in replay.get("evidence_step_ids", [])
            or artifact_id not in replay.get("artifact_ids", [])
        ):
            raise ValueError(f"AFK invalid replay does not support its correction: {artifact_id}")
        prior_state_id = sample.get("candidate_label_hint")
        if not isinstance(prior_state_id, str) or not prior_state_id.strip():
            raise ValueError(f"AFK corrected sample has no route endpoint label: {artifact_id}")
        if prior_state_id == AFK_ADJUDICATED_STATE_ID:
            raise ValueError(f"AFK correction does not contradict its old label: {artifact_id}")
        sample["candidate_label_hint"] = AFK_ADJUDICATED_STATE_ID
        sample["candidate_label_origin"] = "evidence-adjudicated-candidate-v4"
        corrections.append(
            {
                "sample_id": str(sample["id"]),
                "artifact_id": artifact_id,
                "evidence_run_id": run_id,
                "evidence_step_id": step_id,
                "evidence_role": str(sample["evidence_role"]),
                "prior_state_id": prior_state_id,
                "adjudicated_state_id": AFK_ADJUDICATED_STATE_ID,
            }
        )
    return {
        "schema": "ai-player-real-image-holdout-candidate-correction-source.v1",
        "kind": "afk-evidence-adjudication-not-human-truth",
        "path": resolved_path.relative_to(reader.repository_root).as_posix(),
        "sha256": actual_sha256,
        "semantic_status": "evidence_backed_candidate",
        "human_truth_status": "not_signed",
        "corrections": corrections,
    }


def build_candidate_corpus(
    db_path: Path,
    *,
    cutoff_started_at: str,
    afk_adjudication_path: Path | None = None,
    expected_afk_adjudication_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a deterministic candidate corpus from a read-only canonical snapshot."""

    with _ReadOnlyEvidence(db_path) as reader:
        runs = reader.list_runs(cutoff_started_at)
        afk_runs = [
            run
            for run in runs
            if run.game_id == "afk-journey"
            and run.adapter == "mumu"
            and run.environment.get("route_start_state")
            and run.environment.get("route_end_state")
            and run.step_ids
            and not _run_has_holdout_ineligible_lifecycle_step(reader, run)
        ]
        sanguo_runs = [
            run
            for run in runs
            if run.game_id == "nslg"
            and run.adapter == "mumu"
            and run.scope_id
            and "post-login" in run.scope_id
            and run.step_ids
            and not _run_has_holdout_ineligible_lifecycle_step(reader, run)
        ]

        collected: list[dict[str, Any]] = []
        for run in afk_runs:
            first = reader.get_step(run.step_ids[0])
            last = reader.get_step(run.step_ids[-1])
            if first and first.before_frame_id:
                collected.append(
                    _sample(
                        reader,
                        game_id="afk-journey",
                        run=run,
                        step=first,
                        role="before",
                        screenshot_id=first.before_frame_id,
                        ui_tree_id=first.before_ui_tree_id,
                        label_hint=str(run.environment["route_start_state"]),
                        label_origin="ai-authored-route-endpoint-metadata",
                    )
                )
            if last and last.after_frame_id:
                collected.append(
                    _sample(
                        reader,
                        game_id="afk-journey",
                        run=run,
                        step=last,
                        role="after",
                        screenshot_id=last.after_frame_id,
                        ui_tree_id=last.after_ui_tree_id,
                        label_hint=str(run.environment["route_end_state"]),
                        label_origin="ai-authored-route-endpoint-metadata",
                    )
                )

        sanguo_samples_by_run_role: dict[tuple[str, str], dict[str, Any]] = {}
        for run in sanguo_runs:
            first = reader.get_step(run.step_ids[0])
            last = reader.get_step(run.step_ids[-1])
            if first and first.before_frame_id:
                sample = _sample(
                    reader,
                    game_id="sanguo-mouding-tianxia",
                    run=run,
                    step=first,
                    role="before",
                    screenshot_id=first.before_frame_id,
                    ui_tree_id=first.before_ui_tree_id,
                    label_hint=None,
                    label_origin=None,
                )
                collected.append(sample)
                sanguo_samples_by_run_role[(run.id, "before")] = sample
            if last and last.after_frame_id:
                sample = _sample(
                    reader,
                    game_id="sanguo-mouding-tianxia",
                    run=run,
                    step=last,
                    role="after",
                    screenshot_id=last.after_frame_id,
                    ui_tree_id=last.after_ui_tree_id,
                    label_hint=None,
                    label_origin=None,
                )
                collected.append(sample)
                sanguo_samples_by_run_role[(run.id, "after")] = sample

        samples, duplicate_count = _deduplicate_samples(collected)
        family_assignments = _source_family_assignments(samples, reader)
        for sample in samples:
            sample["source_family_id"] = family_assignments[sample["id"]]
        if (afk_adjudication_path is None) != (
            expected_afk_adjudication_sha256 is None
        ):
            raise ValueError(
                "AFK adjudication path and detached expected hash must be provided together"
            )
        correction_source = None
        if afk_adjudication_path is not None:
            correction_source = _apply_afk_evidence_adjudication(
                reader,
                samples,
                adjudication_path=afk_adjudication_path,
                expected_sha256=str(expected_afk_adjudication_sha256),
            )
        kept_ids = {sample["id"] for sample in samples}
        sanguo_samples_by_run_role = {
            key: sample
            for key, sample in sanguo_samples_by_run_role.items()
            if sample["id"] in kept_ids
        }
        afk_samples = [item for item in samples if item["game_id"] == "afk-journey"]
        afk_reference, afk_holdout, afk_partitions = _partition_afk(afk_samples)
        sanguo_reference, sanguo_holdout, sanguo_partitions = _partition_sanguo_boundaries(
            sanguo_runs, sanguo_samples_by_run_role
        )
        evidence_samples = [
            {
                key: value
                for key, value in sample.items()
                if key not in {"candidate_label_hint", "candidate_label_origin"}
            }
            for sample in samples
        ]

        fixture = {
            "schema": FIXTURE_SCHEMA_V3 if correction_source else FIXTURE_SCHEMA,
            "purpose": FIXTURE_PURPOSE,
            "generated_at": cutoff_started_at,
            "source_store": {
                "root_path": str(reader.root),
                "database_path": str(reader.db_path),
                "read_mode": "sqlite-mode-ro-query-only-transaction",
                "cutoff_started_at": cutoff_started_at,
            },
            "generator": {
                "implementation": "ai_player.real_image_holdout.build_candidate_corpus",
                "version": GENERATOR_VERSION_V3 if correction_source else GENERATOR_VERSION,
            },
            "samples": evidence_samples,
            "generation_summary": {
                "sample_count": len(samples),
                "afk_sample_count": len(afk_samples),
                "sanguo_postlogin_sample_count": len(samples) - len(afk_samples),
                "exact_duplicate_excluded_count": duplicate_count,
                "afk_evidence_run_count": len(afk_runs),
                "sanguo_postlogin_evidence_run_count": len(sanguo_runs),
                "source_family_count": len(set(family_assignments.values())),
            },
        }
        fixture_sha256 = _hash_bytes(_json_bytes(fixture))
        combined_reference = sorted(
            afk_reference + sanguo_reference,
            key=lambda item: item["sample_id"],
        )
        combined_holdout = sorted(
            afk_holdout + sanguo_holdout,
            key=lambda item: item["sample_id"],
        )
        evidence_sample_by_id = {sample["id"]: sample for sample in evidence_samples}
        critical_states_by_game = {
            game_id: sorted(
                {
                    entry["semantic_state_id"]
                    for entry in combined_holdout
                    if evidence_sample_by_id[entry["sample_id"]]["game_id"] == game_id
                }
            )
            for game_id in SUPPORTED_GAMES
        }
        used_sample_ids = {
            entry["sample_id"] for entry in combined_reference + combined_holdout
        }
        used_family_ids = {
            evidence_sample_by_id[sample_id]["source_family_id"]
            for sample_id in used_sample_ids
        }
        combined_partitions = {**afk_partitions, **sanguo_partitions}
        labels = {
            "schema": LABEL_SCHEMA_V3 if correction_source else LABEL_SCHEMA,
            "purpose": CANDIDATE_LABEL_PURPOSE,
            "fixture_sha256": fixture_sha256,
            "adjudication": {
                "status": "candidate_unreviewed",
                "reviewer": None,
                "signed_at": None,
                "attestation": None,
                "warning": (
                    "这些标签来自 AI 运行元数据或时序边界推断，不能充当独立真值。"
                ),
            },
            "critical_state_ids_by_game": critical_states_by_game,
            "reference_labels": combined_reference,
            "holdout_labels": combined_holdout,
            "source_family_partitions": {
                key: value
                for key, value in sorted(combined_partitions.items())
                if key in used_family_ids
            },
        }
        if correction_source is not None:
            labelled_by_sample = {
                str(entry["sample_id"]): entry
                for entry in combined_reference + combined_holdout
            }
            for correction in correction_source["corrections"]:
                labelled = labelled_by_sample.get(str(correction["sample_id"]))
                if labelled is None:
                    correction["disposition"] = "excluded-unpaired-semantic-state"
                    correction["reason"] = (
                        "corrected state has no disjoint reference family at the frozen cutoff"
                    )
                else:
                    if labelled["semantic_state_id"] != correction["adjudicated_state_id"]:
                        raise ValueError("AFK correction was not preserved in candidate labels")
                    correction["disposition"] = "corrected-and-retained"
                    correction["reason"] = "corrected state has disjoint reference and holdout families"
            labels["candidate_correction_source"] = correction_source
    return fixture, labels


def write_candidate_corpus(
    db_path: Path,
    fixture_path: Path,
    labels_path: Path,
    *,
    cutoff_started_at: str,
    afk_adjudication_path: Path | None = None,
    expected_afk_adjudication_sha256: str | None = None,
) -> dict[str, Any]:
    fixture, labels = build_candidate_corpus(
        db_path,
        cutoff_started_at=cutoff_started_at,
        afk_adjudication_path=afk_adjudication_path,
        expected_afk_adjudication_sha256=expected_afk_adjudication_sha256,
    )
    fixture_payload = _json_bytes(fixture)
    labels_payload = _json_bytes(labels)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(fixture_payload)
    labels_path.write_bytes(labels_payload)
    return {
        "fixture_path": str(fixture_path.resolve()),
        "fixture_sha256": _hash_bytes(fixture_payload),
        "labels_path": str(labels_path.resolve()),
        "labels_sha256": _hash_bytes(labels_payload),
        "summary": fixture["generation_summary"],
        "reference_label_count": len(labels["reference_labels"]),
        "holdout_label_count": len(labels["holdout_labels"]),
    }


def _validate_binding(
    reader: _ReadOnlyEvidence,
    binding: Mapping[str, Any],
    *,
    expected_kind: str,
) -> ArtifactRef:
    _require_exact_keys(
        binding,
        {
            "artifact_id",
            "kind",
            "canonical_path",
            "sha256",
            "capture_run_id",
            "media_type",
        },
        "artifact binding",
    )
    artifact = reader.verified_artifact(
        str(binding["artifact_id"]), expected_kind=expected_kind
    )
    if binding["kind"] != artifact.kind:
        raise ValueError(f"fixture kind mismatch: {artifact.id}")
    if Path(str(binding["canonical_path"])).resolve() != Path(artifact.path).resolve():
        raise ValueError(f"fixture canonical path mismatch: {artifact.id}")
    if binding["sha256"] != artifact.sha256:
        raise ValueError(f"fixture artifact hash mismatch: {artifact.id}")
    if binding["capture_run_id"] != artifact.run_id:
        raise ValueError(f"fixture capture run mismatch: {artifact.id}")
    return artifact


def _validate_fixture(
    reader: _ReadOnlyEvidence,
    fixture: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    _require_exact_keys(
        fixture,
        {
            "schema",
            "purpose",
            "generated_at",
            "source_store",
            "generator",
            "samples",
            "generation_summary",
        },
        "fixture",
    )
    if fixture["schema"] not in {FIXTURE_SCHEMA, FIXTURE_SCHEMA_V3}:
        raise ValueError(
            f"fixture schema must be {FIXTURE_SCHEMA} or {FIXTURE_SCHEMA_V3}"
        )
    if fixture["purpose"] != FIXTURE_PURPOSE:
        raise ValueError("fixture purpose is not the frozen candidate purpose")
    source_store = fixture["source_store"]
    if not isinstance(source_store, Mapping):
        raise ValueError("fixture source_store must be an object")
    _require_exact_keys(
        source_store,
        {
            "root_path",
            "database_path",
            "read_mode",
            "cutoff_started_at",
        },
        "fixture source_store",
    )
    if Path(str(source_store["root_path"])).resolve() != reader.root:
        raise ValueError("fixture source root does not match evaluated database")
    if Path(str(source_store["database_path"])).resolve() != reader.db_path:
        raise ValueError("fixture database path does not match evaluated database")
    if source_store["read_mode"] != "sqlite-mode-ro-query-only-transaction":
        raise ValueError("fixture read mode is unsupported")
    cutoff = _parse_aware_datetime(
        source_store["cutoff_started_at"],
        "fixture cutoff_started_at",
    )
    if fixture["generated_at"] != source_store["cutoff_started_at"]:
        raise ValueError("fixture generated_at must equal its frozen cutoff")
    generator = fixture["generator"]
    if not isinstance(generator, Mapping):
        raise ValueError("fixture generator must be an object")
    _require_exact_keys(generator, {"implementation", "version"}, "fixture generator")
    expected_generator_version = (
        GENERATOR_VERSION_V3
        if fixture["schema"] == FIXTURE_SCHEMA_V3
        else GENERATOR_VERSION
    )
    if generator != {
        "implementation": "ai_player.real_image_holdout.build_candidate_corpus",
        "version": expected_generator_version,
    }:
        raise ValueError("fixture generator is not code-pinned")
    samples = fixture["samples"]
    if not isinstance(samples, list) or not samples:
        raise ValueError("fixture samples must be a non-empty list")
    by_id: dict[str, Mapping[str, Any]] = {}
    artifact_ids: set[str] = set()
    artifact_hashes: set[str] = set()
    canonical_paths: set[str] = set()
    for raw in samples:
        if not isinstance(raw, Mapping):
            raise ValueError("fixture sample must be an object")
        _require_exact_keys(
            raw,
            {
                "id",
                "game_id",
                "viewport_width",
                "viewport_height",
                "screenshot",
                "ui_tree",
                "evidence_run_id",
                "evidence_step_id",
                "evidence_role",
                "build_scope_id",
                "evidence_scope_id",
                "source_group_id",
                "source_family_id",
            },
            "fixture sample",
        )
        sample_id = str(raw["id"])
        if sample_id in by_id:
            raise ValueError(f"duplicate sample id: {sample_id}")
        if raw["game_id"] not in SUPPORTED_GAMES:
            raise ValueError(f"unsupported fixture game: {raw['game_id']}")
        if raw["evidence_role"] not in {"before", "after"}:
            raise ValueError(f"unsupported evidence role: {sample_id}")
        screenshot = _validate_binding(reader, raw["screenshot"], expected_kind="screenshot")
        artifact_id = screenshot.id
        canonical_path = str(Path(screenshot.path).resolve())
        if artifact_id in artifact_ids:
            raise ValueError(f"duplicate screenshot artifact: {artifact_id}")
        if screenshot.sha256 in artifact_hashes:
            raise ValueError(f"duplicate screenshot content: {screenshot.sha256}")
        if canonical_path in canonical_paths:
            raise ValueError(f"duplicate screenshot path: {canonical_path}")
        artifact_ids.add(artifact_id)
        artifact_hashes.add(screenshot.sha256)
        canonical_paths.add(canonical_path)
        ui_tree = None
        if raw["ui_tree"] is not None:
            ui_tree = _validate_binding(reader, raw["ui_tree"], expected_kind="ui_tree")
        run_id = raw["evidence_run_id"]
        step_id = raw["evidence_step_id"]
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(f"sample requires an evidence run: {sample_id}")
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError(f"sample requires an evidence step: {sample_id}")
        run = reader.get_run(run_id)
        step = reader.get_step(step_id)
        if run is None:
            raise ValueError(f"unknown evidence run: {run_id}")
        if step is None:
            raise ValueError(f"unknown evidence step: {step_id}")
        if step.evidence_run_id != run.id or step.id not in run.step_ids:
            raise ValueError(f"run/step relationship mismatch: {sample_id}")
        if run.status in {"running", "paused"} or not run.ended_at:
            raise ValueError(f"sample evidence run is not terminal: {sample_id}")
        if step.status == "running" or not step.ended_at:
            raise ValueError(f"sample evidence step is not terminal: {sample_id}")
        if run.adapter != "mumu":
            raise ValueError(f"sample evidence run is not MuMu: {sample_id}")
        if _parse_aware_datetime(run.started_at, "run.started_at") > cutoff:
            raise ValueError(f"sample evidence run exceeds fixture cutoff: {sample_id}")
        expected_game_id = (
            "sanguo-mouding-tianxia" if run.game_id == "nslg" else run.game_id
        )
        if raw["game_id"] != expected_game_id:
            raise ValueError(f"sample game does not match evidence run: {sample_id}")
        if raw["build_scope_id"] != run.build_scope_id:
            raise ValueError(f"sample build scope does not match evidence run: {sample_id}")
        if raw["evidence_scope_id"] != run.scope_id:
            raise ValueError(f"sample evidence scope does not match evidence run: {sample_id}")
        if raw["viewport_width"] != run.viewport_width or raw["viewport_height"] != run.viewport_height:
            raise ValueError(f"sample viewport does not match evidence run: {sample_id}")
        if raw["game_id"] == "sanguo-mouding-tianxia" and "post-login" not in str(
            raw["evidence_scope_id"]
        ):
            raise ValueError(f"Sanguo sample is not from a post-login run: {sample_id}")
        if raw["source_group_id"] != run_id:
            raise ValueError(f"source group must be the evidence run: {sample_id}")
        if not isinstance(raw["source_family_id"], str) or not raw[
            "source_family_id"
        ].strip():
            raise ValueError(f"sample requires a source family: {sample_id}")

        role = str(raw["evidence_role"])
        expected_screenshot_id = (
            step.before_frame_id if role == "before" else step.after_frame_id
        )
        expected_ui_tree_id = (
            step.before_ui_tree_id if role == "before" else step.after_ui_tree_id
        )
        if screenshot.id != expected_screenshot_id:
            raise ValueError(f"{role} screenshot artifact does not match EvidenceStep: {sample_id}")
        if (ui_tree.id if ui_tree else None) != expected_ui_tree_id:
            raise ValueError(f"{role} UI artifact does not match EvidenceStep: {sample_id}")
        for artifact in (screenshot, ui_tree):
            if artifact is None:
                continue
            metadata_run_id = artifact.metadata.get("evidence_run_id")
            metadata_step_id = artifact.metadata.get("evidence_step_id")
            if metadata_run_id is not None and metadata_run_id != run.id:
                raise ValueError(f"artifact evidence_run_id mismatch: {artifact.id}")
            if metadata_step_id is not None and metadata_step_id != step.id:
                raise ValueError(f"artifact evidence_step_id mismatch: {artifact.id}")
        by_id[sample_id] = raw

    expected_families = _source_family_assignments(list(by_id.values()), reader)
    for sample_id, expected_family in expected_families.items():
        if by_id[sample_id]["source_family_id"] != expected_family:
            raise ValueError(f"source_family_id does not match canonical family: {sample_id}")

    summary = fixture["generation_summary"]
    if not isinstance(summary, Mapping):
        raise ValueError("fixture generation_summary must be an object")
    _require_exact_keys(
        summary,
        {
            "sample_count",
            "afk_sample_count",
            "sanguo_postlogin_sample_count",
            "exact_duplicate_excluded_count",
            "afk_evidence_run_count",
            "sanguo_postlogin_evidence_run_count",
            "source_family_count",
        },
        "fixture generation_summary",
    )
    cutoff_runs = reader.list_runs(str(source_store["cutoff_started_at"]))
    eligible_afk_runs = [
        run
        for run in cutoff_runs
        if run.game_id == "afk-journey"
        and run.adapter == "mumu"
        and run.environment.get("route_start_state")
        and run.environment.get("route_end_state")
        and run.step_ids
    ]
    eligible_sanguo_runs = [
        run
        for run in cutoff_runs
        if run.game_id == "nslg"
        and run.adapter == "mumu"
        and run.scope_id
        and "post-login" in run.scope_id
        and run.step_ids
    ]
    expected_counts = {
        "sample_count": len(by_id),
        "afk_sample_count": sum(
            item["game_id"] == "afk-journey" for item in by_id.values()
        ),
        "sanguo_postlogin_sample_count": sum(
            item["game_id"] == "sanguo-mouding-tianxia" for item in by_id.values()
        ),
        "afk_evidence_run_count": len(eligible_afk_runs),
        "sanguo_postlogin_evidence_run_count": len(eligible_sanguo_runs),
        "source_family_count": len(set(expected_families.values())),
    }
    for key, expected_value in expected_counts.items():
        if summary[key] != expected_value:
            raise ValueError(f"fixture generation summary mismatch: {key}")
    if not isinstance(summary["exact_duplicate_excluded_count"], int) or summary[
        "exact_duplicate_excluded_count"
    ] < 0:
        raise ValueError("fixture duplicate exclusion count is invalid")
    return by_id


def _validate_labels(
    labels: Mapping[str, Any],
    *,
    fixture_sha256: str,
    labels_sha256: str,
    samples: Mapping[str, Mapping[str, Any]],
    reader: _ReadOnlyEvidence,
) -> tuple[
    list[Mapping[str, str]],
    list[Mapping[str, str]],
    dict[str, set[str]],
    bool,
    bool,
]:
    label_schema = labels.get("schema")
    if label_schema not in {LABEL_SCHEMA, LABEL_SCHEMA_V3}:
        raise ValueError(f"label schema must be {LABEL_SCHEMA} or {LABEL_SCHEMA_V3}")
    expected_label_keys = {
        "schema",
        "purpose",
        "fixture_sha256",
        "adjudication",
        "critical_state_ids_by_game",
        "reference_labels",
        "holdout_labels",
        "source_family_partitions",
    }
    if label_schema == LABEL_SCHEMA_V3:
        expected_label_keys.add("candidate_correction_source")
    _require_exact_keys(
        labels,
        expected_label_keys,
        "labels",
    )
    if labels["purpose"] not in {CANDIDATE_LABEL_PURPOSE, SIGNED_LABEL_PURPOSE}:
        raise ValueError("label purpose is unsupported")
    if labels["fixture_sha256"] != fixture_sha256:
        raise ValueError("label file is bound to a different fixture hash")
    reference = labels["reference_labels"]
    holdout = labels["holdout_labels"]
    if not isinstance(reference, list) or not isinstance(holdout, list):
        raise ValueError("label partitions must be lists")
    seen_samples: set[str] = set()
    reference_families: set[str] = set()
    holdout_families: set[str] = set()
    for partition_name, entries, families in (
        ("reference", reference, reference_families),
        ("holdout", holdout, holdout_families),
    ):
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValueError(f"{partition_name} label must be an object")
            _require_exact_keys(
                entry,
                {"sample_id", "semantic_state_id", "label_origin", "label_locator"},
                f"{partition_name} label",
            )
            sample_id = str(entry["sample_id"])
            if sample_id not in samples:
                raise ValueError(f"label references unknown sample: {sample_id}")
            if sample_id in seen_samples:
                raise ValueError(f"sample appears in multiple label entries: {sample_id}")
            seen_samples.add(sample_id)
            if not str(entry["semantic_state_id"]).strip():
                raise ValueError(f"empty semantic state id: {sample_id}")
            families.add(str(samples[sample_id]["source_family_id"]))
    leaked_families = reference_families & holdout_families
    if leaked_families:
        raise ValueError(
            f"source families leak across partitions: {sorted(leaked_families)}"
        )
    declared_partitions = labels["source_family_partitions"]
    if not isinstance(declared_partitions, Mapping):
        raise ValueError("source family partitions must be an object")
    used_families = reference_families | holdout_families
    if set(declared_partitions) != used_families:
        raise ValueError("declared source family partitions do not match labelled families")
    for family in reference_families:
        if declared_partitions.get(family) != "reference":
            raise ValueError(f"incorrect declared reference source partition: {family}")
    for family in holdout_families:
        if declared_partitions.get(family) != "holdout":
            raise ValueError(f"incorrect declared holdout source partition: {family}")

    reference_ids = [str(entry["sample_id"]) for entry in reference]
    holdout_ids = [str(entry["sample_id"]) for entry in holdout]
    hash_cache: dict[str, str] = {}
    pixel_cache: dict[str, np.ndarray] = {}
    for sample_id in reference_ids + holdout_ids:
        path = Path(str(samples[sample_id]["screenshot"]["canonical_path"]))
        hash_cache[sample_id] = _dhash(path)
        pixel_cache[sample_id] = _pixel_signature(path)
    for reference_id in reference_ids:
        for holdout_id in holdout_ids:
            if samples[reference_id]["game_id"] != samples[holdout_id]["game_id"]:
                continue
            if _near_duplicate(
                hash_cache[reference_id],
                hash_cache[holdout_id],
                pixel_cache[reference_id],
                pixel_cache[holdout_id],
            ):
                raise ValueError(
                    "perceptual or pixel near-duplicate leaks across partitions: "
                    f"{reference_id} / {holdout_id}"
                )

    raw_critical = labels["critical_state_ids_by_game"]
    if not isinstance(raw_critical, Mapping) or set(raw_critical) != set(SUPPORTED_GAMES):
        raise ValueError("critical state inventory must cover exactly both supported games")
    critical_states: dict[str, set[str]] = {}
    for game_id in SUPPORTED_GAMES:
        values = raw_critical[game_id]
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item.strip() for item in values
        ):
            raise ValueError(f"critical states must be non-empty strings: {game_id}")
        if len(values) != len(set(values)):
            raise ValueError(f"critical states contain duplicates: {game_id}")
        labelled_states = {
            str(entry["semantic_state_id"])
            for entry in reference + holdout
            if samples[str(entry["sample_id"])]["game_id"] == game_id
        }
        if not set(values).issubset(labelled_states):
            raise ValueError(f"critical states are absent from labels: {game_id}")
        critical_states[game_id] = set(values)

    adjudication = labels["adjudication"]
    if not isinstance(adjudication, Mapping):
        raise ValueError("label adjudication must be an object")
    _require_exact_keys(
        adjudication,
        {"status", "reviewer", "signed_at", "attestation", "warning"},
        "label adjudication",
    )
    independently_signed = adjudication["status"] == "independent_signed"
    if independently_signed:
        if labels["purpose"] != SIGNED_LABEL_PURPOSE:
            raise ValueError("independently signed labels require the signed purpose")
        reviewer = adjudication["reviewer"]
        attestation = adjudication["attestation"]
        if not isinstance(reviewer, Mapping) or not isinstance(attestation, Mapping):
            raise ValueError("independent labels require reviewer and detached attestation")
        _require_exact_keys(reviewer, {"kind", "id"}, "independent reviewer")
        _require_exact_keys(attestation, {"path"}, "detached attestation locator")
        reviewer_id = str(reviewer["id"])
        registry_entry = TRUSTED_REVIEWER_REGISTRY.get(reviewer_id)
        if registry_entry is None:
            raise ValueError(f"independent reviewer is not trusted: {reviewer_id}")
        _require_exact_keys(
            registry_entry,
            {"kind", "public_key_base64", "status"},
            "trusted reviewer registry entry",
        )
        if registry_entry["status"] != "trusted":
            raise ValueError(f"independent reviewer is not active: {reviewer_id}")
        if reviewer["kind"] != registry_entry["kind"]:
            raise ValueError("independent reviewer kind does not match registry")
        signed_at = adjudication.get("signed_at")
        _parse_aware_datetime(signed_at, "adjudication signed_at")
        relative_attestation = Path(str(attestation["path"]))
        if relative_attestation.is_absolute():
            raise ValueError("detached adjudication attestation path must be repository-relative")
        attestation_path = (reader.repository_root / relative_attestation).resolve()
        if not _inside(attestation_path, reader.repository_root):
            raise ValueError("detached adjudication attestation must stay inside repository")
        if not attestation_path.is_file():
            raise ValueError("detached adjudication attestation does not exist")
        attestation_body = json.loads(attestation_path.read_text(encoding="utf-8"))
        if not isinstance(attestation_body, Mapping):
            raise ValueError("detached adjudication attestation must be an object")
        _require_exact_keys(
            attestation_body,
            {
                "schema",
                "reviewer",
                "fixture_sha256",
                "labels_sha256",
                "verdict",
                "generated_at",
                "adjudication_body",
                "signature_algorithm",
                "signature_base64",
            },
            "detached adjudication attestation",
        )
        if attestation_body["schema"] != ATTESTATION_SCHEMA:
            raise ValueError("detached adjudication attestation schema is unsupported")
        if attestation_body["reviewer"] != dict(reviewer):
            raise ValueError("attestation reviewer does not match labels")
        if attestation_body["fixture_sha256"] != fixture_sha256:
            raise ValueError("attestation fixture hash does not match labels")
        if attestation_body["labels_sha256"] != labels_sha256:
            raise ValueError("attestation labels hash does not match detached labels")
        if attestation_body["verdict"] != "approved_for_g02_ground_truth":
            raise ValueError("attestation verdict does not approve G02 ground truth")
        if attestation_body["generated_at"] != signed_at:
            raise ValueError("attestation generation time does not match signed_at")
        _parse_aware_datetime(attestation_body["generated_at"], "attestation generated_at")
        if not isinstance(attestation_body["adjudication_body"], str) or len(
            attestation_body["adjudication_body"].strip()
        ) < 20:
            raise ValueError("attestation requires a substantive adjudication body")
        if attestation_body["signature_algorithm"] != "ed25519":
            raise ValueError("attestation signature algorithm is unsupported")
        signed_payload = {
            key: value for key, value in attestation_body.items() if key != "signature_base64"
        }
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(registry_entry["public_key_base64"], validate=True)
            )
            signature = base64.b64decode(
                str(attestation_body["signature_base64"]),
                validate=True,
            )
            public_key.verify(
                signature,
                json.dumps(
                    signed_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
        except (ValueError, TypeError, InvalidSignature) as error:
            raise ValueError("detached adjudication signature is invalid") from error
        if any(entry["label_origin"] != "independent-adjudication" for entry in reference + holdout):
            raise ValueError("signed labels must identify independent adjudication as origin")
    elif adjudication["status"] == "candidate_unreviewed":
        if labels["purpose"] != CANDIDATE_LABEL_PURPOSE:
            raise ValueError("candidate labels require the candidate purpose")
        if any(adjudication[field] is not None for field in ("reviewer", "signed_at", "attestation")):
            raise ValueError("candidate adjudication cannot carry reviewer or attestation data")
    else:
        raise ValueError("unsupported adjudication status")

    correction_source_verified = False
    if label_schema == LABEL_SCHEMA_V3:
        if labels["purpose"] != CANDIDATE_LABEL_PURPOSE or independently_signed:
            raise ValueError("v3 corrected labels remain unsigned candidate material")
        correction_source = labels["candidate_correction_source"]
        if not isinstance(correction_source, Mapping):
            raise ValueError("candidate correction source must be an object")
        _require_exact_keys(
            correction_source,
            {
                "schema",
                "kind",
                "path",
                "sha256",
                "semantic_status",
                "human_truth_status",
                "corrections",
            },
            "candidate correction source",
        )
        if correction_source["schema"] != (
            "ai-player-real-image-holdout-candidate-correction-source.v1"
        ):
            raise ValueError("candidate correction source schema is unsupported")
        if correction_source["kind"] != "afk-evidence-adjudication-not-human-truth":
            raise ValueError("candidate correction source kind is unsupported")
        if (
            correction_source["semantic_status"] != "evidence_backed_candidate"
            or correction_source["human_truth_status"] != "not_signed"
        ):
            raise ValueError("candidate correction source must remain unsigned evidence")
        source_path = Path(str(correction_source["path"]))
        if source_path.is_absolute():
            raise ValueError("candidate correction source path must be repository-relative")
        resolved_source_path = (reader.repository_root / source_path).resolve()
        if not _inside(resolved_source_path, reader.repository_root):
            raise ValueError("candidate correction source escapes the repository")
        if not resolved_source_path.is_file():
            raise ValueError("candidate correction source does not exist")
        if _sha256_file(resolved_source_path) != correction_source["sha256"]:
            raise ValueError("candidate correction source hash mismatch")
        source_body = json.loads(resolved_source_path.read_text(encoding="utf-8"))
        if not isinstance(source_body, Mapping):
            raise ValueError("candidate correction source body must be an object")
        if (
            source_body.get("schema") != AFK_ADJUDICATION_SCHEMA
            or source_body.get("id") != AFK_ADJUDICATION_ID
            or source_body.get("semantic_status") != "evidence_backed_candidate"
            or source_body.get("human_truth_status") != "not_signed"
        ):
            raise ValueError("candidate correction source identity is unsupported")
        source_states = source_body.get("observed_states")
        if not isinstance(source_states, list):
            raise ValueError("candidate correction source states must be a list")
        target_states = [
            state
            for state in source_states
            if isinstance(state, Mapping)
            and state.get("id") == AFK_ADJUDICATED_STATE_ID
        ]
        if len(target_states) != 1:
            raise ValueError("candidate correction source repaired state is missing")
        target_state = target_states[0]
        source_artifact_ids = set(target_state.get("artifact_ids", []))
        source_step_ids = set(target_state.get("evidence_step_ids", []))
        source_replays = source_body.get("invalid_replays")
        if not isinstance(source_replays, list):
            raise ValueError("candidate correction source invalid replays must be a list")
        source_replay_by_run = {
            str(replay.get("evidence_run_id")): replay
            for replay in source_replays
            if isinstance(replay, Mapping)
            and isinstance(replay.get("evidence_run_id"), str)
        }
        corrections = correction_source["corrections"]
        if not isinstance(corrections, list) or len(corrections) != (
            AFK_ADJUDICATED_CONFLICT_COUNT
        ):
            raise ValueError("candidate correction inventory must contain six conflicts")
        labelled_by_sample = {
            str(entry["sample_id"]): entry for entry in reference + holdout
        }
        corrected_artifacts: set[str] = set()
        corrected_samples: set[str] = set()
        for correction in corrections:
            if not isinstance(correction, Mapping):
                raise ValueError("candidate correction must be an object")
            _require_exact_keys(
                correction,
                {
                    "sample_id",
                    "artifact_id",
                    "evidence_run_id",
                    "evidence_step_id",
                    "evidence_role",
                    "prior_state_id",
                    "adjudicated_state_id",
                    "disposition",
                    "reason",
                },
                "candidate correction",
            )
            sample_id = str(correction["sample_id"])
            artifact_id = str(correction["artifact_id"])
            if sample_id in corrected_samples or artifact_id in corrected_artifacts:
                raise ValueError("candidate correction inventory contains duplicates")
            corrected_samples.add(sample_id)
            corrected_artifacts.add(artifact_id)
            sample = samples.get(sample_id)
            if sample is None or sample["game_id"] != "afk-journey":
                raise ValueError(f"candidate correction references invalid sample: {sample_id}")
            if (
                sample["screenshot"]["artifact_id"] != artifact_id
                or sample["evidence_run_id"] != correction["evidence_run_id"]
                or sample["evidence_step_id"] != correction["evidence_step_id"]
                or sample["evidence_role"] != correction["evidence_role"]
            ):
                raise ValueError(f"candidate correction evidence binding mismatch: {sample_id}")
            source_replay = source_replay_by_run.get(str(correction["evidence_run_id"]))
            if (
                artifact_id not in source_artifact_ids
                or correction["evidence_step_id"] not in source_step_ids
                or source_replay is None
                or correction["evidence_step_id"]
                not in source_replay.get("evidence_step_ids", [])
                or artifact_id not in source_replay.get("artifact_ids", [])
                or source_replay.get("replay_status") != "invalid_start_state"
                or source_replay.get("observed_start_state_id")
                != AFK_ADJUDICATED_STATE_ID
                or source_replay.get("observed_end_state_id")
                != AFK_ADJUDICATED_STATE_ID
            ):
                raise ValueError(f"candidate correction is absent from source evidence: {sample_id}")
            run = reader.get_run(str(correction["evidence_run_id"]))
            if run is None:
                raise ValueError(f"candidate correction run is missing: {sample_id}")
            route_key = (
                "route_start_state"
                if correction["evidence_role"] == "before"
                else "route_end_state"
            )
            if run.environment.get(route_key) != correction["prior_state_id"]:
                raise ValueError(f"candidate correction prior state mismatch: {sample_id}")
            if correction["adjudicated_state_id"] != AFK_ADJUDICATED_STATE_ID:
                raise ValueError(f"candidate correction target state mismatch: {sample_id}")
            if not isinstance(correction["reason"], str) or not correction["reason"].strip():
                raise ValueError(f"candidate correction requires a reason: {sample_id}")
            labelled = labelled_by_sample.get(sample_id)
            if correction["disposition"] == "excluded-unpaired-semantic-state":
                if labelled is not None:
                    raise ValueError(f"excluded correction remains labelled: {sample_id}")
            elif correction["disposition"] == "corrected-and-retained":
                if labelled is None or labelled["semantic_state_id"] != (
                    correction["adjudicated_state_id"]
                ):
                    raise ValueError(f"retained correction label mismatch: {sample_id}")
            else:
                raise ValueError(f"candidate correction disposition is unsupported: {sample_id}")
        if corrected_artifacts != source_artifact_ids:
            raise ValueError("candidate correction inventory does not cover source artifacts")
        correction_source_verified = True
    return (
        reference,
        holdout,
        critical_states,
        independently_signed,
        correction_source_verified,
    )


@lru_cache(maxsize=4096)
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


@lru_cache(maxsize=4096)
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
            mean = float(resized.mean())
            bits = (resized >= mean).flatten()
            value = 0
            for bit in bits:
                value = (value << 1) | int(bit)
            output[f"grid-{row}-{column}"] = f"{value:016x}"
    return output


@lru_cache(maxsize=4096)
def _ui_tokens(path: Path | None) -> tuple[list[str], list[str], list[str]]:
    if path is None:
        return [], [], []
    root = ET.parse(path).getroot()
    structure: set[str] = set()
    text: set[str] = set()
    selected: set[str] = set()
    for node in root.iter():
        class_name = node.attrib.get("class")
        resource_id = node.attrib.get("resource-id")
        if class_name:
            structure.add(f"class:{class_name}")
        if resource_id:
            structure.add(f"resource-id:{resource_id}")
        for attribute in ("text", "content-desc", "hint"):
            value = node.attrib.get(attribute, "").strip()
            if value:
                text.add(f"{attribute}:{value}")
        if node.attrib.get("selected") == "true":
            identity = resource_id or node.attrib.get("text") or class_name
            if identity:
                selected.add(str(identity))
    return sorted(structure), sorted(text), sorted(selected)


def _features(sample: Mapping[str, Any]) -> StateObservationFeaturesV1:
    screenshot_path = Path(sample["screenshot"]["canonical_path"])
    ui_path = (
        Path(sample["ui_tree"]["canonical_path"])
        if sample.get("ui_tree") is not None
        else None
    )
    structure, text, selected = _ui_tokens(ui_path)
    orientation = (
        "portrait"
        if int(sample["viewport_height"]) > int(sample["viewport_width"])
        else "landscape"
    )
    return StateObservationFeaturesV1(
        screenshot_fingerprint=_dhash(screenshot_path),
        ui_structure_tokens=structure,
        ui_text_tokens=text,
        runtime_tokens=[
            f"game:{sample['game_id']}",
            f"viewport:{sample['viewport_width']}x{sample['viewport_height']}",
            f"orientation:{orientation}",
        ],
        selected_object_tokens=selected,
        region_fingerprints=_region_hashes(screenshot_path),
    )


def _predict(
    samples: Mapping[str, Mapping[str, Any]],
    reference_labels: Sequence[Mapping[str, str]],
    holdout_sample_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Predict holdouts without receiving their expected semantic labels."""

    feature_cache: dict[str, StateObservationFeaturesV1] = {}

    def feature(sample_id: str) -> StateObservationFeaturesV1:
        if sample_id not in feature_cache:
            feature_cache[sample_id] = _features(samples[sample_id])
        return feature_cache[sample_id]

    prototypes: dict[str, list[str]] = defaultdict(list)
    for entry in reference_labels:
        prototypes[entry["semantic_state_id"]].append(entry["sample_id"])
    predictions: list[dict[str, Any]] = []
    for sample_id in holdout_sample_ids:
        ranked: list[tuple[str, float]] = []
        for state_id, prototype_ids in prototypes.items():
            score = max(
                state_observation_similarity(feature(sample_id), feature(prototype_id))[0]
                for prototype_id in prototype_ids
            )
            ranked.append((state_id, score))
        ranked.sort(key=lambda item: (-item[1], item[0]))
        best = ranked[0] if ranked else None
        runner_up = ranked[1] if len(ranked) > 1 else None
        recognized = bool(
            best
            and best[1] >= POLICY["match_threshold"]
            and (
                runner_up is None
                or best[1] - runner_up[1] >= POLICY["ambiguity_margin"]
            )
        )
        predictions.append(
            {
                "sample_id": sample_id,
                "predicted_state_id": best[0] if recognized and best else None,
                "disposition": "recognized_existing" if recognized else "needs_adjudication",
                "best_score": round(best[1], 8) if best else None,
                "runner_up_score": round(runner_up[1], 8) if runner_up else None,
                "ranked_matches": [
                    {"state_id": state_id, "score": round(score, 8)}
                    for state_id, score in ranked[:10]
                ],
            }
        )
    return predictions


def evaluate_candidate_corpus(
    db_path: Path,
    fixture_path: Path,
    labels_path: Path,
    *,
    expected_fixture_sha256: str,
    expected_labels_sha256: str,
) -> dict[str, Any]:
    fixture_actual_hash = _sha256_file(fixture_path)
    labels_actual_hash = _sha256_file(labels_path)
    if fixture_actual_hash != expected_fixture_sha256:
        raise ValueError("fixture file does not match the detached expected hash")
    if labels_actual_hash != expected_labels_sha256:
        raise ValueError("label file does not match the detached expected hash")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if (fixture.get("schema") == FIXTURE_SCHEMA_V3) != (
        labels.get("schema") == LABEL_SCHEMA_V3
    ):
        raise ValueError("fixture and label schema generations do not match")
    with _ReadOnlyEvidence(db_path) as reader:
        samples = _validate_fixture(reader, fixture)
        (
            reference,
            holdout,
            critical_states,
            independently_signed,
            correction_source_verified,
        ) = _validate_labels(
            labels,
            fixture_sha256=fixture_actual_hash,
            labels_sha256=labels_actual_hash,
            samples=samples,
            reader=reader,
        )

    holdout_sample_ids = [str(entry["sample_id"]) for entry in holdout]
    predictions = _predict(samples, reference, holdout_sample_ids)
    expected = {
        str(entry["sample_id"]): str(entry["semantic_state_id"]) for entry in holdout
    }
    scored: list[dict[str, Any]] = []
    correct_count = 0
    for prediction in predictions:
        sample_id = str(prediction["sample_id"])
        game_id = str(samples[sample_id]["game_id"])
        expected_state = expected[sample_id]
        predicted_state = prediction["predicted_state_id"]
        correct = predicted_state == expected_state
        false_merge = bool(predicted_state is not None and not correct)
        over_split = bool(predicted_state is None and not correct)
        critical_false_merge = bool(
            false_merge
            and (
                expected_state in critical_states[game_id]
                or predicted_state in critical_states[game_id]
            )
        )
        correct_count += int(correct)
        scored.append(
            {
                **prediction,
                "candidate_expected_state_id": expected_state,
                "candidate_label_agrees": correct,
                "critical_false_merge": critical_false_merge,
                "overall_false_merge": false_merge,
                "over_split": over_split,
            }
        )
    overall_accuracy = correct_count / len(scored) if scored else None

    holdout_unique_states: dict[str, set[str]] = defaultdict(set)
    for entry in holdout:
        game_id = str(samples[str(entry["sample_id"])]["game_id"])
        holdout_unique_states[game_id].add(str(entry["semantic_state_id"]))
    scored_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scored:
        scored_by_game[str(samples[str(item["sample_id"])]["game_id"])].append(item)

    candidate_metrics_by_game: dict[str, dict[str, Any]] = {}
    coverage_by_game: dict[str, dict[str, Any]] = {}
    gate_by_game: dict[str, dict[str, Any]] = {}
    for game_id in SUPPORTED_GAMES:
        game_scored = scored_by_game[game_id]
        count = len(game_scored)
        correct = sum(bool(item["candidate_label_agrees"]) for item in game_scored)
        critical_false_merges = sum(
            bool(item["critical_false_merge"]) for item in game_scored
        )
        false_merges = sum(bool(item["overall_false_merge"]) for item in game_scored)
        over_splits = sum(bool(item["over_split"]) for item in game_scored)
        accuracy = correct / count if count else None
        false_merge_rate = false_merges / count if count else None
        over_split_rate = over_splits / count if count else None
        states = holdout_unique_states[game_id]
        critical_holdout_states = states & critical_states[game_id]
        requirements = MINIMUM_HOLDOUT_REQUIREMENTS[game_id]
        coverage = {
            "required_holdout_samples": requirements["samples"],
            "candidate_holdout_samples": count,
            "sample_shortfall": max(0, requirements["samples"] - count),
            "required_holdout_unique_states": requirements["unique_states"],
            "candidate_holdout_unique_states": len(states),
            "state_shortfall": max(0, requirements["unique_states"] - len(states)),
            "required_holdout_critical_states": requirements["critical_states"],
            "candidate_holdout_critical_states": len(critical_holdout_states),
            "critical_state_shortfall": max(
                0,
                requirements["critical_states"] - len(critical_holdout_states),
            ),
            "independently_signed_holdout_samples": count if independently_signed else 0,
            "independently_signed_holdout_unique_states": len(states)
            if independently_signed
            else 0,
            "independently_signed_holdout_critical_states": len(
                critical_holdout_states
            )
            if independently_signed
            else 0,
        }
        coverage_by_game[game_id] = coverage
        candidate_metrics_by_game[game_id] = {
            "holdout_label_count": count,
            "holdout_unique_state_count": len(states),
            "critical_holdout_state_count": len(critical_holdout_states),
            "candidate_label_agreement_count": correct,
            "candidate_label_agreement_rate": accuracy,
            "critical_false_merge_count": critical_false_merges,
            "overall_false_merge_count": false_merges,
            "overall_false_merge_rate": false_merge_rate,
            "over_split_count": over_splits,
            "over_split_rate": over_split_rate,
        }
        coverage_complete = not any(
            coverage[key]
            for key in ("sample_shortfall", "state_shortfall", "critical_state_shortfall")
        )
        accuracy_pass = bool(
            independently_signed
            and accuracy is not None
            and accuracy >= POLICY["minimum_accuracy"]
        )
        error_gates_pass = bool(
            independently_signed
            and critical_false_merges
            <= POLICY["maximum_critical_false_merge_count"]
            and false_merge_rate is not None
            and false_merge_rate <= POLICY["maximum_overall_false_merge_rate"]
            and over_split_rate is not None
            and over_split_rate <= POLICY["maximum_over_split_rate"]
        )
        game_eligible = independently_signed and coverage_complete
        gate_by_game[game_id] = {
            "eligible": game_eligible,
            "coverage_complete": coverage_complete,
            "measured_independent_accuracy": accuracy if independently_signed else None,
            "critical_false_merge_count": critical_false_merges
            if independently_signed
            else None,
            "overall_false_merge_rate": false_merge_rate if independently_signed else None,
            "over_split_rate": over_split_rate if independently_signed else None,
            "accuracy_pass": accuracy_pass,
            "error_gates_pass": error_gates_pass,
            "pass": bool(game_eligible and accuracy_pass and error_gates_pass),
        }

    reasons: list[str] = []
    if not independently_signed:
        reasons.append("候选标签未经过可信独立签发，诊断一致率不能作为准确率。")
    for game_id, coverage in coverage_by_game.items():
        if any(
            coverage[key]
            for key in ("sample_shortfall", "state_shortfall", "critical_state_shortfall")
        ):
            reasons.append(f"{game_id} 的 holdout 样本或状态覆盖未达到固定下限。")
    if not scored:
        reasons.append("没有可评分的 holdout 样本。")
    eligible = independently_signed and all(
        item["eligible"] for item in gate_by_game.values()
    )
    g02_pass = bool(eligible and all(item["pass"] for item in gate_by_game.values()))
    registry_payload = {
        key: value for key, value in sorted(TRUSTED_REVIEWER_REGISTRY.items())
    }
    payload = {
        "schema": RESULT_SCHEMA,
        "purpose": RESULT_PURPOSE,
        "facility_status": "PASS",
        "g02_real_image_holdout_status": "PASS" if g02_pass else "FAIL",
        "fixture_path": str(fixture_path.resolve()),
        "fixture_sha256": fixture_actual_hash,
        "labels_path": str(labels_path.resolve()),
        "labels_sha256": labels_actual_hash,
        "policy": POLICY,
        "policy_sha256": POLICY_SHA256,
        "policy_source": "code-pinned; fixture and label files cannot change thresholds",
        "candidate_diagnostic": {
            "reference_label_count": len(reference),
            "holdout_label_count": len(holdout),
            "holdout_count_by_game": {
                game_id: len(scored_by_game[game_id]) for game_id in SUPPORTED_GAMES
            },
            "by_game": candidate_metrics_by_game,
            "candidate_label_agreement_count": correct_count,
            "candidate_label_agreement_rate": overall_accuracy,
            "warning": "该指标只比较未审候选标签，不是 G-02 独立留出集准确率。",
        },
        "independent_adjudication": {
            "signed": independently_signed,
            "trusted_reviewer_registry_sha256": _hash_bytes(
                _json_bytes(registry_payload)
            ),
            "candidate_holdout_labels_still_requiring_independent_signature": len(holdout)
            if not independently_signed
            else 0,
            "candidate_all_labels_still_requiring_independent_signature": len(
                reference + holdout
            )
            if not independently_signed
            else 0,
            "holdout_coverage_by_game": coverage_by_game,
        },
        "g02_gate": {
            "eligible": eligible,
            "minimum_accuracy": POLICY["minimum_accuracy"],
            "measured_independent_accuracy_by_game": {
                game_id: gate_by_game[game_id]["measured_independent_accuracy"]
                for game_id in SUPPORTED_GAMES
            },
            "by_game": gate_by_game,
            "pass": g02_pass,
            "reasons": reasons,
        },
        "facility_checks": {
            "fixture_detached_hash_verified": True,
            "labels_detached_hash_verified": True,
            "canonical_artifact_rows_and_file_hashes_verified": True,
            "duplicate_screenshot_ids_paths_and_hashes_rejected": True,
            "reference_holdout_source_families_disjoint": True,
            "perceptual_and_pixel_near_duplicates_disjoint": True,
            "step_frame_and_ui_bindings_verified": True,
            "terminal_mumu_cutoff_verified": True,
            "trusted_reviewer_ed25519_attestation_verified": independently_signed,
            "prediction_does_not_receive_holdout_expected_labels": True,
            "thresholds_code_pinned": True,
            **(
                {"candidate_correction_source_verified": True}
                if correction_source_verified
                else {}
            ),
        },
        "sample_results": scored,
    }
    return {**payload, "result_sha256": _hash_bytes(_json_bytes(payload))}


def write_evaluation_result(
    db_path: Path,
    fixture_path: Path,
    labels_path: Path,
    output_path: Path,
    *,
    expected_fixture_sha256: str,
    expected_labels_sha256: str,
) -> dict[str, Any]:
    result = evaluate_candidate_corpus(
        db_path,
        fixture_path,
        labels_path,
        expected_fixture_sha256=expected_fixture_sha256,
        expected_labels_sha256=expected_labels_sha256,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_json_bytes(result))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-candidate")
    build.add_argument(
        "--db",
        type=Path,
        default=default_observatory_root() / "observatory.sqlite3",
    )
    build.add_argument("--cutoff-started-at", required=True)
    build.add_argument("--fixture", type=Path, required=True)
    build.add_argument("--labels", type=Path, required=True)
    build.add_argument("--afk-adjudication", type=Path)
    build.add_argument("--expected-afk-adjudication-sha256")
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument(
        "--db",
        type=Path,
        default=default_observatory_root() / "observatory.sqlite3",
    )
    evaluate.add_argument("--fixture", type=Path, required=True)
    evaluate.add_argument("--labels", type=Path, required=True)
    evaluate.add_argument("--expected-fixture-sha256", required=True)
    evaluate.add_argument("--expected-labels-sha256", required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build-candidate":
        result = write_candidate_corpus(
            args.db,
            args.fixture,
            args.labels,
            cutoff_started_at=args.cutoff_started_at,
            afk_adjudication_path=args.afk_adjudication,
            expected_afk_adjudication_sha256=(
                args.expected_afk_adjudication_sha256
            ),
        )
    else:
        result = write_evaluation_result(
            args.db,
            args.fixture,
            args.labels,
            args.output,
            expected_fixture_sha256=args.expected_fixture_sha256,
            expected_labels_sha256=args.expected_labels_sha256,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
