"""Fail-closed AFK known-control benchmark for interaction preflight decisions.

The benchmark deliberately separates input cases, human-frozen labels and producer
predictions.  Candidate manifests and unsigned labels are never treated as truth.
"""

from __future__ import annotations

import argparse
import base64
import gc
import hashlib
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import ArtifactRef, NormalizedAction, SourcePixelPoint, SourcePixelRect
from ..store import ObservatoryStore
from . import real_image_holdout
from .afk_human_truth_freeze import validate_frozen_afk_human_truth
from .contracts import (
    EnvironmentScopeV1,
    SemanticStateV1,
    StateAssignmentV1,
    StateObservationV1,
    TransitionEdgeV1,
)
from .interaction_preflight_producer import (
    InteractionPreflightProducer,
    InteractionPreflightProductionRequestV1,
)
from .store import AIPlayerStore


BENCHMARK_ID = "afk_interaction_preflight_known_controls_v1"
FIXTURE_SCHEMA = "game-observatory.ai-player.afk-interaction-preflight-fixture.v1"
LABELS_SCHEMA = "game-observatory.ai-player.afk-interaction-preflight-labels.v1"
ATTESTATION_SCHEMA = (
    "game-observatory.ai-player.afk-interaction-preflight-attestation.v1"
)
PREDICTIONS_SCHEMA = (
    "game-observatory.ai-player.afk-interaction-preflight-predictions.v1"
)
RESULT_SCHEMA = "game-observatory.ai-player.afk-interaction-preflight-result.v1"
APPROVAL_VERDICT = "approved_for_afk_interaction_preflight_benchmark"

MIN_CASES = 20
MIN_PASS_CASES = 8
MIN_REJECT_CASES = 8
MIN_OVERLAY_UNDERLAYER_CASES = 2
MIN_SELECTED_TAB_CASES = 2
MIN_SOURCE_FAMILIES = 8
REQUIRED_COORDINATE_VARIANTS = frozenset(
    {"native_portrait", "rotated_landscape", "scaled_letterbox", "rotated_letterbox"}
)
MIN_ACCURACY = 0.95

SHA256_PATTERN = r"^[0-9a-f]{64}$"
CaseKind = Literal[
    "actionable_control",
    "non_interactive",
    "overlay_underlayer",
    "selected_tab",
    "coordinate_transform",
]
CoordinateVariant = Literal[
    "native_portrait",
    "rotated_landscape",
    "scaled_letterbox",
    "rotated_letterbox",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ImmutableFileRefV1(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_PATTERN)


class HumanReviewerV1(_StrictModel):
    kind: Literal["human_reviewer"]
    id: str = Field(min_length=1)


class FrozenTruthRootV1(_StrictModel):
    candidate_manifest: ImmutableFileRefV1
    import_manifest: ImmutableFileRefV1
    frozen_manifest: ImmutableFileRefV1


class BenchmarkEvidenceRefV1(_StrictModel):
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_PATTERN)


class AFKInteractionPreflightInputCaseV1(_StrictModel):
    id: str = Field(min_length=1)
    source_artifact_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_PATTERN)
    source_family_id: str = Field(min_length=1)
    replay_packet: ImmutableFileRefV1
    candidate_id: str = Field(min_length=1)
    producer_request_sha256: str = Field(
        min_length=64, max_length=64, pattern=SHA256_PATTERN
    )
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    coordinate_variant: CoordinateVariant


class CoordinateProjectionV1(_StrictModel):
    rotation_degrees: Literal[0, 90, 180, 270]
    scale_x: float = Field(gt=0)
    scale_y: float = Field(gt=0)
    offset_x: float = Field(ge=0)
    offset_y: float = Field(ge=0)
    device_viewport_width: int = Field(gt=0)
    device_viewport_height: int = Field(gt=0)


class AFKInteractionPreflightReplayPacketV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.afk-interaction-preflight-replay-packet.v1"
    ] = Field(
        default=(
            "game-observatory.ai-player.afk-interaction-preflight-replay-packet.v1"
        ),
        alias="schema",
    )
    environment: EnvironmentScopeV1
    artifacts: tuple[ArtifactRef, ...] = Field(min_length=1)
    semantic_states: tuple[SemanticStateV1, ...] = Field(min_length=1)
    observation: StateObservationV1
    assignment: StateAssignmentV1
    transition_edges: tuple[TransitionEdgeV1, ...] = ()
    producer_request: InteractionPreflightProductionRequestV1
    action: NormalizedAction
    projection: CoordinateProjectionV1

    @model_validator(mode="after")
    def bind_replay_environment(self) -> "AFKInteractionPreflightReplayPacketV1":
        environment_id = self.environment.id
        entities = [
            *self.semantic_states,
            self.observation,
            self.assignment,
            *self.transition_edges,
        ]
        if any(entity.environment_id != environment_id for entity in entities):
            raise ValueError("replay packet entities must share one environment")
        if self.producer_request.environment_id != environment_id:
            raise ValueError("replay producer request belongs to another environment")
        artifact_ids = {artifact.id for artifact in self.artifacts}
        if self.producer_request.source_artifact_id not in artifact_ids:
            raise ValueError("replay packet omits the producer source artifact")
        return self


class AFKInteractionPreflightFixtureV1(_StrictModel):
    schema_id: Literal[FIXTURE_SCHEMA] = Field(default=FIXTURE_SCHEMA, alias="schema")
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    truth_root: FrozenTruthRootV1
    cases: tuple[AFKInteractionPreflightInputCaseV1, ...] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def unique_case_ids(
        cls, value: tuple[AFKInteractionPreflightInputCaseV1, ...]
    ) -> tuple[AFKInteractionPreflightInputCaseV1, ...]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark fixture case ids must be unique")
        return value


class AFKInteractionPreflightTruthCaseV1(_StrictModel):
    case_id: str = Field(min_length=1)
    input_case_sha256: str = Field(
        min_length=64, max_length=64, pattern=SHA256_PATTERN
    )
    case_kind: CaseKind
    expected_disposition: Literal["passed", "rejected"]
    expected_bounds: SourcePixelRect
    expected_source_point: SourcePixelPoint | None = None
    expected_device_point: SourcePixelPoint | None = None
    expected_roundtrip_source_point: SourcePixelPoint | None = None
    max_error_px: float = Field(default=1.0, ge=0, le=5)
    human_reason: str = Field(min_length=20)
    evidence_refs: tuple[BenchmarkEvidenceRefV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bind_expected_points(self) -> "AFKInteractionPreflightTruthCaseV1":
        points = (
            self.expected_source_point,
            self.expected_device_point,
            self.expected_roundtrip_source_point,
        )
        if self.expected_disposition == "passed" and any(point is None for point in points):
            raise ValueError("passed truth cases require source, device and roundtrip points")
        if self.expected_disposition == "rejected" and any(point is not None for point in points):
            raise ValueError("rejected truth cases cannot prescribe action points")
        if self.case_kind in {"overlay_underlayer", "selected_tab", "non_interactive"}:
            if self.expected_disposition != "rejected":
                raise ValueError(f"{self.case_kind} truth must require rejection")
        if len(self.human_reason.strip()) < 20:
            raise ValueError("human_reason must contain at least 20 substantive characters")
        return self


class AFKInteractionPreflightLabelsV1(_StrictModel):
    schema_id: Literal[LABELS_SCHEMA] = Field(default=LABELS_SCHEMA, alias="schema")
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    fixture_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_PATTERN)
    truth_status: Literal["human_frozen"]
    reviewer: HumanReviewerV1
    signed_at: datetime
    cases: tuple[AFKInteractionPreflightTruthCaseV1, ...] = Field(min_length=1)

    @field_validator("signed_at")
    @classmethod
    def aware_signed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("labels signed_at must include a timezone")
        return value

    @field_validator("cases")
    @classmethod
    def unique_case_ids(
        cls, value: tuple[AFKInteractionPreflightTruthCaseV1, ...]
    ) -> tuple[AFKInteractionPreflightTruthCaseV1, ...]:
        ids = [item.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark truth case ids must be unique")
        return value


class AFKInteractionPreflightAttestationV1(_StrictModel):
    schema_id: Literal[ATTESTATION_SCHEMA] = Field(
        default=ATTESTATION_SCHEMA, alias="schema"
    )
    reviewer: HumanReviewerV1
    fixture_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_PATTERN)
    labels_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_PATTERN)
    verdict: Literal[APPROVAL_VERDICT]
    generated_at: datetime
    adjudication_body: str = Field(min_length=20)
    signature_algorithm: Literal["ed25519"]
    signature_base64: str = Field(min_length=1)

    @field_validator("generated_at")
    @classmethod
    def aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attestation generated_at must include a timezone")
        return value

    @field_validator("adjudication_body")
    @classmethod
    def substantive_body(cls, value: str) -> str:
        if len(value.strip()) < 20:
            raise ValueError("attestation adjudication_body must be substantive")
        return value


class AFKInteractionPreflightPredictionCaseV1(_StrictModel):
    case_id: str = Field(min_length=1)
    input_case_sha256: str = Field(
        min_length=64, max_length=64, pattern=SHA256_PATTERN
    )
    source_artifact_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    production_result_sha256: str = Field(
        min_length=64, max_length=64, pattern=SHA256_PATTERN
    )
    disposition: Literal["passed", "rejected"]
    candidate_bounds: SourcePixelRect | None = None
    source_point: SourcePixelPoint | None = None
    device_point: SourcePixelPoint | None = None
    roundtrip_source_point: SourcePixelPoint | None = None
    would_dispatch_pointer: bool

    @model_validator(mode="after")
    def bind_dispatch_to_disposition(self) -> "AFKInteractionPreflightPredictionCaseV1":
        values = (
            self.candidate_bounds,
            self.source_point,
            self.device_point,
            self.roundtrip_source_point,
        )
        if self.disposition == "passed":
            if not self.would_dispatch_pointer or any(value is None for value in values):
                raise ValueError("passed predictions require complete coordinates and dispatch")
        elif self.would_dispatch_pointer or any(value is not None for value in values):
            raise ValueError("rejected predictions must preserve zero pointer dispatch")
        return self


class AFKInteractionPreflightPredictionsV1(_StrictModel):
    schema_id: Literal[PREDICTIONS_SCHEMA] = Field(
        default=PREDICTIONS_SCHEMA, alias="schema"
    )
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    fixture_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_PATTERN)
    producer: Literal["interaction-preflight-producer.v1"]
    producer_code_sha256: str = Field(
        min_length=64, max_length=64, pattern=SHA256_PATTERN
    )
    generated_at: datetime
    cases: tuple[AFKInteractionPreflightPredictionCaseV1, ...] = Field(min_length=1)

    @field_validator("generated_at")
    @classmethod
    def aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("prediction generated_at must include a timezone")
        return value

    @field_validator("cases")
    @classmethod
    def unique_case_ids(
        cls, value: tuple[AFKInteractionPreflightPredictionCaseV1, ...]
    ) -> tuple[AFKInteractionPreflightPredictionCaseV1, ...]:
        ids = [item.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("prediction case ids must be unique")
        return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_file(repository_root: Path, reference: ImmutableFileRefV1) -> Path:
    relative = Path(reference.path)
    if relative.is_absolute():
        raise ValueError("benchmark evidence paths must be repository-relative")
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("benchmark evidence path escapes repository root") from error
    if not resolved.is_file() or _sha256_file(resolved) != reference.sha256:
        raise ValueError(f"benchmark evidence file or hash is invalid: {reference.path}")
    return resolved


def _input_case_hash(case: AFKInteractionPreflightInputCaseV1) -> str:
    return _sha256_bytes(_canonical_bytes(case.model_dump(mode="json", by_alias=True)))


def _distance(left: SourcePixelPoint, right: SourcePixelPoint) -> float:
    return math.hypot(left.x - right.x, left.y - right.y)


def _producer_code_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(
        {
            Path(__file__).resolve(),
            Path(__file__).with_name("interaction_preflight.py").resolve(),
            Path(__file__).with_name("interaction_preflight_producer.py").resolve(),
        },
        key=str,
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _project_point(
    point: SourcePixelPoint,
    *,
    source_width: int,
    source_height: int,
    projection: CoordinateProjectionV1,
) -> tuple[SourcePixelPoint, SourcePixelPoint]:
    x, y = point.x, point.y
    if projection.rotation_degrees == 0:
        rotated_x, rotated_y = x, y
    elif projection.rotation_degrees == 90:
        rotated_x, rotated_y = source_height - 1 - y, x
    elif projection.rotation_degrees == 180:
        rotated_x, rotated_y = source_width - 1 - x, source_height - 1 - y
    else:
        rotated_x, rotated_y = y, source_width - 1 - x
    device = SourcePixelPoint(
        x=round(projection.offset_x + rotated_x * projection.scale_x),
        y=round(projection.offset_y + rotated_y * projection.scale_y),
    )
    if (
        device.x >= projection.device_viewport_width
        or device.y >= projection.device_viewport_height
    ):
        raise ValueError("projected device point exceeds the replay device viewport")
    inverse_x = round((device.x - projection.offset_x) / projection.scale_x)
    inverse_y = round((device.y - projection.offset_y) / projection.scale_y)
    if projection.rotation_degrees == 0:
        source_x, source_y = inverse_x, inverse_y
    elif projection.rotation_degrees == 90:
        source_x, source_y = inverse_y, source_height - 1 - inverse_x
    elif projection.rotation_degrees == 180:
        source_x, source_y = source_width - 1 - inverse_x, source_height - 1 - inverse_y
    else:
        source_x, source_y = source_width - 1 - inverse_y, inverse_x
    return device, SourcePixelPoint(x=source_x, y=source_y)


def _resolved_artifact(
    repository_root: Path,
    artifact: ArtifactRef,
) -> ArtifactRef:
    reference = ImmutableFileRefV1(path=artifact.path, sha256=artifact.sha256)
    path = _resolve_file(repository_root, reference)
    return artifact.model_copy(update={"path": str(path)})


def build_producer_predictions(
    *,
    repository_root: Path,
    fixture_path: Path,
    expected_fixture_sha256: str,
    prediction_id: str,
    generated_at: datetime | None = None,
) -> AFKInteractionPreflightPredictionsV1:
    """Replay fixture packets through the real producer in an isolated store."""

    fixture_hash = _sha256_file(fixture_path)
    if fixture_hash != expected_fixture_sha256:
        raise ValueError("fixture does not match detached SHA-256")
    fixture = AFKInteractionPreflightFixtureV1.model_validate_json(
        fixture_path.read_bytes()
    )
    prediction_cases: list[AFKInteractionPreflightPredictionCaseV1] = []
    with tempfile.TemporaryDirectory(prefix="afk-preflight-benchmark-") as directory:
        observatory = ObservatoryStore(Path(directory) / "observatory")
        player = AIPlayerStore(observatory)
        producer = InteractionPreflightProducer(player)
        for case in fixture.cases:
            packet_path = _resolve_file(repository_root, case.replay_packet)
            packet = AFKInteractionPreflightReplayPacketV1.model_validate_json(
                packet_path.read_bytes()
            )
            request_hash = _sha256_bytes(
                _canonical_bytes(
                    packet.producer_request.model_dump(mode="json", by_alias=True)
                )
            )
            if (
                request_hash != case.producer_request_sha256
                or packet.producer_request.candidate_id != case.candidate_id
            ):
                raise ValueError(f"replay producer request does not match fixture: {case.id}")
            artifacts = [
                _resolved_artifact(repository_root, artifact)
                for artifact in packet.artifacts
            ]
            source = next(
                (
                    artifact
                    for artifact in artifacts
                    if artifact.id == packet.producer_request.source_artifact_id
                ),
                None,
            )
            if source is None:
                raise ValueError(f"replay source artifact is missing: {case.id}")
            if (
                source.id != case.source_artifact_id
                or source.sha256 != case.source_sha256
                or Path(source.path).resolve()
                != (repository_root.resolve() / case.source_path).resolve()
            ):
                raise ValueError(f"replay source does not match fixture identity: {case.id}")
            if (
                packet.environment.viewport_width != case.viewport_width
                or packet.environment.viewport_height != case.viewport_height
            ):
                raise ValueError(f"replay viewport does not match fixture: {case.id}")
            for artifact in artifacts:
                observatory.save_artifact(artifact)
            player.put_environment(packet.environment)
            for state in packet.semantic_states:
                player.put_semantic_state(state)
            player.append_state_observation(packet.observation)
            player.append_state_assignment(packet.assignment)
            for edge in packet.transition_edges:
                player.put_transition_edge(edge)
            result = producer.produce(packet.producer_request)
            result_hash = _sha256_bytes(
                _canonical_bytes(result.model_dump(mode="json", by_alias=True))
            )
            if result.disposition == "passed":
                if packet.action.x is None or packet.action.y is None:
                    raise ValueError(f"passed replay action has no source point: {case.id}")
                assert result.preflight is not None
                source_point = SourcePixelPoint(x=packet.action.x, y=packet.action.y)
                device_point, roundtrip = _project_point(
                    source_point,
                    source_width=case.viewport_width,
                    source_height=case.viewport_height,
                    projection=packet.projection,
                )
                candidate_bounds = result.preflight.candidate_bounds
                would_dispatch = True
            else:
                source_point = None
                device_point = None
                roundtrip = None
                candidate_bounds = None
                would_dispatch = False
            prediction_cases.append(
                AFKInteractionPreflightPredictionCaseV1(
                    case_id=case.id,
                    input_case_sha256=_input_case_hash(case),
                    source_artifact_id=case.source_artifact_id,
                    candidate_id=case.candidate_id,
                    production_result_sha256=result_hash,
                    disposition=result.disposition,
                    candidate_bounds=candidate_bounds,
                    source_point=source_point,
                    device_point=device_point,
                    roundtrip_source_point=roundtrip,
                    would_dispatch_pointer=would_dispatch,
                )
            )
        del producer, player, observatory
        gc.collect()
    return AFKInteractionPreflightPredictionsV1(
        id=prediction_id,
        fixture_sha256=fixture_hash,
        producer="interaction-preflight-producer.v1",
        producer_code_sha256=_producer_code_sha256(),
        generated_at=generated_at or datetime.now(timezone.utc),
        cases=tuple(prediction_cases),
    )


def _verify_human_attestation(
    *,
    labels: AFKInteractionPreflightLabelsV1,
    attestation: AFKInteractionPreflightAttestationV1,
    fixture_sha256: str,
    labels_sha256: str,
) -> None:
    if attestation.reviewer != labels.reviewer:
        raise ValueError("benchmark attestation reviewer does not match labels")
    if attestation.fixture_sha256 != fixture_sha256:
        raise ValueError("benchmark attestation fixture hash mismatch")
    if attestation.labels_sha256 != labels_sha256:
        raise ValueError("benchmark attestation labels hash mismatch")
    if attestation.generated_at != labels.signed_at:
        raise ValueError("benchmark attestation timestamp does not match labels")
    registry = real_image_holdout.TRUSTED_REVIEWER_REGISTRY.get(labels.reviewer.id)
    if registry is None:
        raise ValueError(f"benchmark human reviewer is not trusted: {labels.reviewer.id}")
    if set(registry) != {"kind", "public_key_base64", "status"}:
        raise ValueError("benchmark reviewer registry entry has unsupported fields")
    if (
        registry["kind"] != "human_reviewer"
        or labels.reviewer.kind != registry["kind"]
        or registry["status"] != "trusted"
    ):
        raise ValueError("benchmark reviewer is not an active trusted human")
    signed_payload = attestation.model_dump(
        mode="json", by_alias=True, exclude={"signature_base64"}
    )
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(registry["public_key_base64"], validate=True)
        )
        signature = base64.b64decode(attestation.signature_base64, validate=True)
        public_key.verify(signature, _canonical_bytes(signed_payload))
    except (ValueError, TypeError, InvalidSignature) as error:
        raise ValueError("benchmark detached human signature is invalid") from error


def audit_candidate_readiness(
    *, candidate_manifest_path: Path, expected_candidate_manifest_sha256: str
) -> dict[str, Any]:
    """Report why an unsigned AFK candidate cannot unlock physical play."""

    actual_hash = _sha256_file(candidate_manifest_path)
    if actual_hash != expected_candidate_manifest_sha256:
        raise ValueError("candidate manifest does not match detached SHA-256")
    raw = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("candidate manifest must be a JSON object")
    signature_count = int(raw.get("counts", {}).get("human_truth_signatures", 0))
    gaps: list[dict[str, str]] = []
    if raw.get("semantic_status") != "human_frozen":
        gaps.append(
            {
                "code": "afk_truth_not_human_frozen",
                "detail": "当前 AFK 清单仍是候选，不能作为预检裁决真值。",
            }
        )
    if raw.get("freeze_pass") is not True or raw.get("frozen") is not True:
        gaps.append(
            {
                "code": "afk_truth_freeze_not_passed",
                "detail": "尚无通过验签的 AFK frozen_manifest。",
            }
        )
    if signature_count < 1 or raw.get("human_truth_signature") is None:
        gaps.append(
            {
                "code": "afk_human_truth_signature_missing",
                "detail": "生产可信人类签名数量为 0。",
            }
        )
    gaps.extend(
        [
            {
                "code": "preflight_fixture_missing",
                "detail": "缺少与 frozen truth 绑定的独立预检输入用例集。",
            },
            {
                "code": "preflight_labels_missing",
                "detail": "缺少人类冻结的 passed/rejected、遮罩、选中页签与落点标签。",
            },
            {
                "code": "preflight_predictions_missing",
                "detail": "缺少 InteractionPreflightProducer 对冻结输入的独立预测。",
            },
        ]
    )
    return {
        "schema": RESULT_SCHEMA,
        "benchmark_id": BENCHMARK_ID,
        "verdict": "FAIL",
        "physical_play_unlocked": False,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "minimum_case_count": MIN_CASES,
            "minimum_pass_cases": MIN_PASS_CASES,
            "minimum_reject_cases": MIN_REJECT_CASES,
            "minimum_source_families": MIN_SOURCE_FAMILIES,
            "minimum_accuracy": MIN_ACCURACY,
            "overlay_underlayer_false_passes": 0,
            "selected_tab_repeat_passes": 0,
            "coordinate_errors": 0,
            "required_coordinate_variants": sorted(REQUIRED_COORDINATE_VARIANTS),
        },
        "source": {
            "candidate_manifest": str(candidate_manifest_path),
            "candidate_manifest_sha256": actual_hash,
            "candidate_id": raw.get("id"),
            "semantic_status": raw.get("semantic_status"),
            "frozen": raw.get("frozen"),
            "freeze_pass": raw.get("freeze_pass"),
            "human_truth_signatures": signature_count,
        },
        "truth_eligible": False,
        "metrics": {
            "case_count": 0,
            "passed_rejected_accuracy": None,
            "overlay_underlayer_false_passes": None,
            "selected_tab_repeat_passes": None,
            "coordinate_errors": None,
        },
        "gaps": gaps,
    }


def evaluate_benchmark(
    *,
    repository_root: Path,
    fixture_path: Path,
    expected_fixture_sha256: str,
    labels_path: Path,
    expected_labels_sha256: str,
    attestation_path: Path,
    expected_attestation_sha256: str,
    predictions_path: Path,
    expected_predictions_sha256: str,
) -> dict[str, Any]:
    """Score current producer predictions against independent human-frozen truth."""

    fixture_hash = _sha256_file(fixture_path)
    labels_hash = _sha256_file(labels_path)
    attestation_hash = _sha256_file(attestation_path)
    predictions_hash = _sha256_file(predictions_path)
    detached = {
        "fixture": (fixture_hash, expected_fixture_sha256),
        "labels": (labels_hash, expected_labels_sha256),
        "attestation": (attestation_hash, expected_attestation_sha256),
        "predictions": (predictions_hash, expected_predictions_sha256),
    }
    mismatched = [name for name, (actual, expected) in detached.items() if actual != expected]
    if mismatched:
        raise ValueError(f"detached benchmark hashes do not match: {sorted(mismatched)}")

    fixture = AFKInteractionPreflightFixtureV1.model_validate_json(
        fixture_path.read_bytes()
    )
    labels = AFKInteractionPreflightLabelsV1.model_validate_json(labels_path.read_bytes())
    attestation = AFKInteractionPreflightAttestationV1.model_validate_json(
        attestation_path.read_bytes()
    )
    predictions = AFKInteractionPreflightPredictionsV1.model_validate_json(
        predictions_path.read_bytes()
    )
    if labels.fixture_sha256 != fixture_hash or predictions.fixture_sha256 != fixture_hash:
        raise ValueError("labels or predictions are bound to another benchmark fixture")
    _verify_human_attestation(
        labels=labels,
        attestation=attestation,
        fixture_sha256=fixture_hash,
        labels_sha256=labels_hash,
    )

    candidate_path = _resolve_file(repository_root, fixture.truth_root.candidate_manifest)
    import_path = _resolve_file(repository_root, fixture.truth_root.import_manifest)
    frozen_path = _resolve_file(repository_root, fixture.truth_root.frozen_manifest)
    validate_frozen_afk_human_truth(
        candidate_manifest_path=candidate_path,
        expected_candidate_manifest_sha256=fixture.truth_root.candidate_manifest.sha256,
        import_manifest_path=import_path,
        frozen_manifest_path=frozen_path,
    )
    replayed_predictions = build_producer_predictions(
        repository_root=repository_root,
        fixture_path=fixture_path,
        expected_fixture_sha256=fixture_hash,
        prediction_id=predictions.id,
        generated_at=predictions.generated_at,
    )
    if replayed_predictions != predictions:
        raise ValueError(
            "prediction file does not match an isolated replay of InteractionPreflightProducer"
        )

    fixture_cases = {item.id: item for item in fixture.cases}
    truth_cases = {item.case_id: item for item in labels.cases}
    prediction_cases = {item.case_id: item for item in predictions.cases}
    if set(fixture_cases) != set(truth_cases) or set(fixture_cases) != set(prediction_cases):
        raise ValueError("fixture, truth and prediction case ids must match exactly")

    for case in fixture.cases:
        source_ref = ImmutableFileRefV1(path=case.source_path, sha256=case.source_sha256)
        _resolve_file(repository_root, source_ref)
        case_hash = _input_case_hash(case)
        truth = truth_cases[case.id]
        prediction = prediction_cases[case.id]
        if truth.input_case_sha256 != case_hash or prediction.input_case_sha256 != case_hash:
            raise ValueError(f"case hash mismatch: {case.id}")
        if not any(
            reference.id == case.source_artifact_id
            and reference.sha256 == case.source_sha256
            for reference in truth.evidence_refs
        ):
            raise ValueError(f"human truth is not bound to the source artifact: {case.id}")
        if (
            prediction.source_artifact_id != case.source_artifact_id
            or prediction.candidate_id != case.candidate_id
        ):
            raise ValueError(f"producer prediction is bound to another input: {case.id}")

    pass_count = sum(
        truth.expected_disposition == "passed" for truth in labels.cases
    )
    reject_count = len(labels.cases) - pass_count
    overlay_count = sum(
        truth.case_kind == "overlay_underlayer" for truth in labels.cases
    )
    selected_count = sum(truth.case_kind == "selected_tab" for truth in labels.cases)
    variants = {case.coordinate_variant for case in fixture.cases}
    source_families = {case.source_family_id for case in fixture.cases}
    coverage_gaps: list[str] = []
    if len(labels.cases) < MIN_CASES:
        coverage_gaps.append(f"case_count<{MIN_CASES}")
    if pass_count < MIN_PASS_CASES:
        coverage_gaps.append(f"pass_cases<{MIN_PASS_CASES}")
    if reject_count < MIN_REJECT_CASES:
        coverage_gaps.append(f"reject_cases<{MIN_REJECT_CASES}")
    if overlay_count < MIN_OVERLAY_UNDERLAYER_CASES:
        coverage_gaps.append(
            f"overlay_underlayer_cases<{MIN_OVERLAY_UNDERLAYER_CASES}"
        )
    if selected_count < MIN_SELECTED_TAB_CASES:
        coverage_gaps.append(f"selected_tab_cases<{MIN_SELECTED_TAB_CASES}")
    if len(source_families) < MIN_SOURCE_FAMILIES:
        coverage_gaps.append(f"source_families<{MIN_SOURCE_FAMILIES}")
    missing_variants = sorted(REQUIRED_COORDINATE_VARIANTS - variants)
    if missing_variants:
        coverage_gaps.append(f"coordinate_variants_missing={missing_variants}")

    correct = 0
    overlay_false_passes = 0
    selected_tab_repeat_passes = 0
    coordinate_errors = 0
    case_results: list[dict[str, Any]] = []
    for case_id in sorted(fixture_cases):
        truth = truth_cases[case_id]
        prediction = prediction_cases[case_id]
        disposition_correct = prediction.disposition == truth.expected_disposition
        correct += int(disposition_correct)
        overlay_false_passes += int(
            truth.case_kind == "overlay_underlayer" and prediction.would_dispatch_pointer
        )
        selected_tab_repeat_passes += int(
            truth.case_kind == "selected_tab" and prediction.would_dispatch_pointer
        )
        errors: list[str] = []
        if prediction.disposition == "passed" and truth.expected_disposition == "passed":
            assert prediction.candidate_bounds is not None
            assert prediction.source_point is not None
            assert prediction.device_point is not None
            assert prediction.roundtrip_source_point is not None
            assert truth.expected_source_point is not None
            assert truth.expected_device_point is not None
            assert truth.expected_roundtrip_source_point is not None
            if prediction.candidate_bounds != truth.expected_bounds:
                errors.append("candidate_bounds_mismatch")
            if not truth.expected_bounds.contains(prediction.source_point):
                errors.append("source_point_outside_truth_bounds")
            if _distance(prediction.source_point, truth.expected_source_point) > truth.max_error_px:
                errors.append("source_point_error")
            if _distance(prediction.device_point, truth.expected_device_point) > truth.max_error_px:
                errors.append("device_point_error")
            if (
                _distance(
                    prediction.roundtrip_source_point,
                    truth.expected_roundtrip_source_point,
                )
                > truth.max_error_px
            ):
                errors.append("roundtrip_source_point_error")
            coordinate_errors += int(bool(errors))
        case_results.append(
            {
                "case_id": case_id,
                "expected_disposition": truth.expected_disposition,
                "actual_disposition": prediction.disposition,
                "disposition_correct": disposition_correct,
                "coordinate_errors": errors,
            }
        )

    accuracy = correct / len(labels.cases)
    gates = {
        "coverage_complete": not coverage_gaps,
        "passed_rejected_accuracy": accuracy >= MIN_ACCURACY,
        "overlay_underlayer_false_passes": overlay_false_passes == 0,
        "selected_tab_repeat_passes": selected_tab_repeat_passes == 0,
        "coordinate_errors": coordinate_errors == 0,
    }
    passed = all(gates.values())
    return {
        "schema": RESULT_SCHEMA,
        "benchmark_id": BENCHMARK_ID,
        "verdict": "PASS" if passed else "FAIL",
        "physical_play_unlocked": passed,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "truth_eligible": True,
        "thresholds": {
            "minimum_case_count": MIN_CASES,
            "minimum_pass_cases": MIN_PASS_CASES,
            "minimum_reject_cases": MIN_REJECT_CASES,
            "minimum_source_families": MIN_SOURCE_FAMILIES,
            "minimum_accuracy": MIN_ACCURACY,
            "overlay_underlayer_false_passes": 0,
            "selected_tab_repeat_passes": 0,
            "coordinate_errors": 0,
            "required_coordinate_variants": sorted(REQUIRED_COORDINATE_VARIANTS),
        },
        "inputs": {
            "fixture_sha256": fixture_hash,
            "labels_sha256": labels_hash,
            "attestation_sha256": attestation_hash,
            "predictions_sha256": predictions_hash,
            "producer_code_sha256": predictions.producer_code_sha256,
        },
        "coverage": {
            "case_count": len(labels.cases),
            "pass_cases": pass_count,
            "reject_cases": reject_count,
            "overlay_underlayer_cases": overlay_count,
            "selected_tab_cases": selected_count,
            "source_families": len(source_families),
            "coordinate_variants": sorted(variants),
            "gaps": coverage_gaps,
        },
        "metrics": {
            "correct_disposition_count": correct,
            "passed_rejected_accuracy": accuracy,
            "overlay_underlayer_false_passes": overlay_false_passes,
            "selected_tab_repeat_passes": selected_tab_repeat_passes,
            "coordinate_errors": coordinate_errors,
        },
        "gates": gates,
        "cases": case_results,
    }


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    readiness = subparsers.add_parser("audit-readiness")
    readiness.add_argument("--candidate-manifest", type=Path, required=True)
    readiness.add_argument("--expected-candidate-manifest-sha256", required=True)
    readiness.add_argument("--output", type=Path, required=True)

    build_predictions = subparsers.add_parser("build-predictions")
    build_predictions.add_argument("--repository-root", type=Path, required=True)
    build_predictions.add_argument("--fixture", type=Path, required=True)
    build_predictions.add_argument("--expected-fixture-sha256", required=True)
    build_predictions.add_argument("--prediction-id", required=True)
    build_predictions.add_argument("--output", type=Path, required=True)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--repository-root", type=Path, required=True)
    evaluate.add_argument("--fixture", type=Path, required=True)
    evaluate.add_argument("--expected-fixture-sha256", required=True)
    evaluate.add_argument("--labels", type=Path, required=True)
    evaluate.add_argument("--expected-labels-sha256", required=True)
    evaluate.add_argument("--attestation", type=Path, required=True)
    evaluate.add_argument("--expected-attestation-sha256", required=True)
    evaluate.add_argument("--predictions", type=Path, required=True)
    evaluate.add_argument("--expected-predictions-sha256", required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "audit-readiness":
            result = audit_candidate_readiness(
                candidate_manifest_path=args.candidate_manifest,
                expected_candidate_manifest_sha256=(
                    args.expected_candidate_manifest_sha256
                ),
            )
        elif args.command == "build-predictions":
            predictions = build_producer_predictions(
                repository_root=args.repository_root,
                fixture_path=args.fixture,
                expected_fixture_sha256=args.expected_fixture_sha256,
                prediction_id=args.prediction_id,
            )
            result = predictions.model_dump(mode="json", by_alias=True)
        else:
            result = evaluate_benchmark(
                repository_root=args.repository_root,
                fixture_path=args.fixture,
                expected_fixture_sha256=args.expected_fixture_sha256,
                labels_path=args.labels,
                expected_labels_sha256=args.expected_labels_sha256,
                attestation_path=args.attestation,
                expected_attestation_sha256=args.expected_attestation_sha256,
                predictions_path=args.predictions,
                expected_predictions_sha256=args.expected_predictions_sha256,
            )
    except Exception as error:  # CLI must preserve a machine-readable FAIL.
        result = {
            "schema": RESULT_SCHEMA,
            "benchmark_id": BENCHMARK_ID,
            "verdict": "FAIL",
            "physical_play_unlocked": False,
            "truth_eligible": False,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "error": {"type": type(error).__name__, "detail": str(error)},
        }
    _write_result(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.command == "build-predictions" and result.get("schema") == PREDICTIONS_SCHEMA:
        return 0
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AFKInteractionPreflightAttestationV1",
    "AFKInteractionPreflightFixtureV1",
    "AFKInteractionPreflightInputCaseV1",
    "AFKInteractionPreflightLabelsV1",
    "AFKInteractionPreflightPredictionCaseV1",
    "AFKInteractionPreflightPredictionsV1",
    "AFKInteractionPreflightReplayPacketV1",
    "AFKInteractionPreflightTruthCaseV1",
    "audit_candidate_readiness",
    "build_producer_predictions",
    "evaluate_benchmark",
]
