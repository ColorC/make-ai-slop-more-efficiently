"""Evidence-bound account metric observations and deterministic deltas."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from ..models import ArtifactRef, EvidenceStep, SourcePixelRect, utc_now
from ..store import ObservatoryStore
from .contracts import ActionQualitySampleV1, EvidenceReferenceV1, PlayerMetricDeltaV1


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


MetricCategory = Literal[
    "account_progression",
    "resource",
    "objective",
    "coverage",
]
MetricFrameRole = Literal["before", "after"]


class AccountMetricDefinitionV1(_StrictModel):
    """Stable interpretation policy fixed before reading either observation."""

    schema_id: Literal["game-observatory.ai-player.account-metric-definition.v1"] = Field(
        default="game-observatory.ai-player.account-metric-definition.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    metric_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    category: MetricCategory
    unit: str = Field(min_length=1)
    numeric_format: Literal["integer", "decimal", "percentage", "compact_zh"]
    improvement_direction: Literal[
        "higher_is_favorable",
        "lower_is_favorable",
        "track_only",
    ]
    minimum_value: float | None = Field(default=None, allow_inf_nan=False)
    maximum_value: float | None = Field(default=None, allow_inf_nan=False)
    minimum_ocr_confidence: float = Field(default=0.90, ge=0.5, le=1)

    @model_validator(mode="after")
    def keep_numeric_range_ordered(self) -> "AccountMetricDefinitionV1":
        if (
            self.minimum_value is not None
            and self.maximum_value is not None
            and self.minimum_value > self.maximum_value
        ):
            raise ValueError("metric minimum value cannot exceed maximum value")
        return self


class AccountMetricObservationRequestV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.account-metric-observation-request.v1"
    ] = Field(
        default="game-observatory.ai-player.account-metric-observation-request.v1",
        alias="schema",
    )
    evidence_step_id: str = Field(min_length=1)
    frame_role: MetricFrameRole
    source_kind: Literal["screenshot_ocr", "authoritative_state"]
    source_artifact_id: str = Field(min_length=1)
    extraction_artifact_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_exact_artifact_shape(self) -> "AccountMetricObservationRequestV1":
        if self.source_kind == "screenshot_ocr" and self.extraction_artifact_id is None:
            raise ValueError("screenshot OCR observation requires an extraction artifact")
        if self.source_kind == "authoritative_state" and self.extraction_artifact_id is not None:
            raise ValueError("authoritative observation uses its source artifact directly")
        return self


class MetricOCRDetectionV1(_StrictModel):
    metric_key: str = Field(min_length=1)
    region: SourcePixelRect
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class MetricOCRExtractionPayloadV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.metric-ocr-extraction.v1"] = Field(
        default="game-observatory.ai-player.metric-ocr-extraction.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    evidence_step_id: str = Field(min_length=1)
    frame_role: MetricFrameRole
    source_artifact_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    detections: list[MetricOCRDetectionV1] = Field(min_length=1)


class AuthoritativeMetricValueV1(_StrictModel):
    metric_key: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)


class AuthoritativeMetricSnapshotPayloadV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.authoritative-metric-snapshot.v1"
    ] = Field(
        default="game-observatory.ai-player.authoritative-metric-snapshot.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    evidence_step_id: str = Field(min_length=1)
    frame_role: MetricFrameRole
    provider: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    record_locator: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    metrics: list[AuthoritativeMetricValueV1] = Field(min_length=1)


class ScreenshotOCRMetricEvidenceV1(_StrictModel):
    method: Literal["screenshot_ocr"] = "screenshot_ocr"
    frame_artifact_id: str = Field(min_length=1)
    frame_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extraction_artifact_id: str = Field(min_length=1)
    extraction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    region: SourcePixelRect
    raw_text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    engine: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)


class AuthoritativeMetricEvidenceV1(_StrictModel):
    method: Literal["authoritative_state"] = "authoritative_state"
    snapshot_artifact_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    record_locator: str = Field(min_length=1)


MetricObservationEvidenceV1 = Annotated[
    ScreenshotOCRMetricEvidenceV1 | AuthoritativeMetricEvidenceV1,
    Field(discriminator="method"),
]


class AccountMetricObservationV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.account-metric-observation.v1"] = Field(
        default="game-observatory.ai-player.account-metric-observation.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    definition: AccountMetricDefinitionV1
    evidence_step_id: str = Field(min_length=1)
    evidence_run_id: str = Field(min_length=1)
    frame_role: MetricFrameRole
    value: float = Field(allow_inf_nan=False)
    observed_at: str = Field(min_length=1)
    source: MetricObservationEvidenceV1


class AccountMetricDeltaDerivationV1(_StrictModel):
    """Portable proof that a PlayerMetricDeltaV1 came from two canonical observations."""

    schema_id: Literal[
        "game-observatory.ai-player.account-metric-delta-derivation.v1"
    ] = Field(
        default="game-observatory.ai-player.account-metric-delta-derivation.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    definition: AccountMetricDefinitionV1
    before_observation: AccountMetricObservationV1
    after_observation: AccountMetricObservationV1
    delta: PlayerMetricDeltaV1
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def keep_portable_proof_consistent(self) -> "AccountMetricDeltaDerivationV1":
        observations = (self.before_observation, self.after_observation)
        if any(item.environment_id != self.environment_id for item in observations):
            raise ValueError("metric observations belong to another environment")
        if any(item.definition != self.definition for item in observations):
            raise ValueError("metric observations use another metric definition")
        step_ids = list(
            dict.fromkeys(item.evidence_step_id for item in observations)
        )
        if self.delta.evidence_step_ids != step_ids:
            raise ValueError("metric delta evidence ids do not match its observations")
        if (
            self.delta.metric_key != self.definition.metric_key
            or self.delta.label != self.definition.label
            or self.delta.category != self.definition.category
            or self.delta.unit != self.definition.unit
            or self.delta.before != self.before_observation.value
            or self.delta.after != self.after_observation.value
        ):
            raise ValueError("metric delta does not match its observations and definition")
        referenced_steps = {
            step_id
            for reference in self.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        referenced_artifacts = {
            artifact_id
            for reference in self.evidence_refs
            for artifact_id in reference.artifact_ids
        }
        if not set(step_ids).issubset(referenced_steps):
            raise ValueError("metric derivation does not retain all evidence steps")
        if not _observation_artifact_ids(observations).issubset(referenced_artifacts):
            raise ValueError("metric derivation does not retain all observation artifacts")
        return self


def _observation_artifact_ids(
    observations: tuple[AccountMetricObservationV1, ...],
) -> set[str]:
    result: set[str] = set()
    for observation in observations:
        source = observation.source
        if isinstance(source, ScreenshotOCRMetricEvidenceV1):
            result.update((source.frame_artifact_id, source.extraction_artifact_id))
        else:
            result.add(source.snapshot_artifact_id)
    return result


def _parse_timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must use ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}.{hashlib.sha256(_canonical_json(value)).hexdigest()[:32]}"


_GROUPED_INTEGER = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)$")
_DECIMAL = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$")
_COMPACT_ZH = re.compile(
    r"^(?P<number>[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)"
    r"(?P<suffix>万|亿)?$"
)


def parse_metric_text(text: str, numeric_format: str) -> float:
    """Parse one unambiguous displayed number; reject prose and multiple candidates."""

    compact = "".join(text.strip().split())
    if numeric_format == "percentage":
        if not compact.endswith("%"):
            raise ValueError("percentage OCR text must end with %")
        compact = compact[:-1]
        pattern = _DECIMAL
        multiplier = Decimal("1")
    elif numeric_format == "integer":
        pattern = _GROUPED_INTEGER
        multiplier = Decimal("1")
    elif numeric_format == "decimal":
        pattern = _DECIMAL
        multiplier = Decimal("1")
    elif numeric_format == "compact_zh":
        match = _COMPACT_ZH.fullmatch(compact)
        if match is None:
            raise ValueError("compact Chinese OCR text is ambiguous")
        compact = match.group("number")
        multiplier = {
            None: Decimal("1"),
            "万": Decimal("10000"),
            "亿": Decimal("100000000"),
        }[match.group("suffix")]
        pattern = _DECIMAL
    else:
        raise ValueError(f"unsupported numeric format: {numeric_format}")
    if pattern.fullmatch(compact) is None:
        raise ValueError("metric OCR text is ambiguous or malformed")
    if numeric_format == "integer" and "." in compact:
        raise ValueError("integer metric cannot contain a decimal point")
    try:
        result = Decimal(compact.replace(",", "")) * multiplier
    except InvalidOperation as error:
        raise ValueError("metric OCR text is not numeric") from error
    value = float(result)
    if not result.is_finite():
        raise ValueError("metric OCR value must be finite")
    return value


class AccountMetricObserver:
    """Resolve observations only from immutable Observatory evidence."""

    def __init__(self, store: ObservatoryStore, environment_id: str) -> None:
        self.store = store
        self.environment_id = environment_id

    def _artifact(self, artifact_id: str) -> ArtifactRef:
        artifact = self.store.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"metric evidence artifact is missing: {artifact_id}")
        path = Path(artifact.path)
        if not path.is_absolute():
            path = self.store.root / path
        path = path.resolve()
        try:
            path.relative_to(self.store.root)
        except ValueError as error:
            raise ValueError(f"metric artifact path escapes canonical root: {artifact.id}") from error
        if not path.is_file():
            raise ValueError(f"metric artifact file is missing: {artifact.id}")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha256 != artifact.sha256:
            raise ValueError(f"metric artifact hash mismatch: {artifact.id}")
        if artifact.metadata.get("environment_id") != self.environment_id:
            raise ValueError(f"metric artifact belongs to another environment: {artifact.id}")
        return artifact

    def _payload(self, artifact: ArtifactRef, adapter: TypeAdapter[Any]) -> Any:
        path = Path(artifact.path)
        if not path.is_absolute():
            path = self.store.root / path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"metric artifact is not valid UTF-8 JSON: {artifact.id}") from error
        return adapter.validate_python(raw)

    def _terminal_step(
        self,
        request: AccountMetricObservationRequestV1,
    ) -> tuple[EvidenceStep, Any]:
        step = self.store.get_evidence_step(request.evidence_step_id)
        if step is None:
            raise ValueError(f"metric evidence step is missing: {request.evidence_step_id}")
        evidence_run = self.store.get_evidence_run(step.evidence_run_id)
        if evidence_run is None:
            raise ValueError(f"metric evidence run is missing: {step.evidence_run_id}")
        if evidence_run.environment.get("environment_id") != self.environment_id:
            raise ValueError("metric evidence run belongs to another environment")
        if step.status != "passed" or evidence_run.status != "passed" or not step.ended_at:
            raise ValueError("metric observations require terminal-passed evidence")
        if step.id not in evidence_run.step_ids:
            raise ValueError("metric evidence step is absent from its run")
        if request.frame_role == "after" and not step.stability.settled:
            raise ValueError("after metric observation requires a settled terminal frame")
        return step, evidence_run

    @staticmethod
    def _require_binding(
        artifact: ArtifactRef,
        *,
        step: EvidenceStep,
        evidence_run_id: str,
        frame_role: MetricFrameRole,
    ) -> None:
        metadata = artifact.metadata
        if (
            metadata.get("evidence_run_id") != evidence_run_id
            or metadata.get("evidence_step_id") != step.id
            or metadata.get("evidence_role") != frame_role
        ):
            raise ValueError(f"metric artifact has an invalid evidence binding: {artifact.id}")

    @staticmethod
    def _range_check(definition: AccountMetricDefinitionV1, value: float) -> None:
        if definition.minimum_value is not None and value < definition.minimum_value:
            raise ValueError("metric value is below its declared range")
        if definition.maximum_value is not None and value > definition.maximum_value:
            raise ValueError("metric value is above its declared range")

    def observe(
        self,
        definition: AccountMetricDefinitionV1,
        request: AccountMetricObservationRequestV1,
    ) -> AccountMetricObservationV1:
        step, evidence_run = self._terminal_step(request)
        source_artifact = self._artifact(request.source_artifact_id)
        self._require_binding(
            source_artifact,
            step=step,
            evidence_run_id=evidence_run.id,
            frame_role=request.frame_role,
        )
        observed_at = source_artifact.captured_at
        if not observed_at:
            raise ValueError("metric source artifact lacks an independent capture time")
        _parse_timestamp(observed_at, "metric source capture time")

        if request.source_kind == "screenshot_ocr":
            expected_frame_id = (
                step.before_frame_id if request.frame_role == "before" else step.after_frame_id
            )
            if source_artifact.id != expected_frame_id or source_artifact.kind != "screenshot":
                raise ValueError("OCR metric source is not the canonical frame for its role")
            extraction = self._artifact(str(request.extraction_artifact_id))
            self._require_binding(
                extraction,
                step=step,
                evidence_run_id=evidence_run.id,
                frame_role=request.frame_role,
            )
            if extraction.kind not in {"trace", "runtime_state", "source"}:
                raise ValueError("OCR extraction artifact has an unsupported kind")
            if extraction.metadata.get("source_artifact_id") != source_artifact.id:
                raise ValueError("OCR extraction metadata names another source frame")
            payload = self._payload(
                extraction,
                TypeAdapter(MetricOCRExtractionPayloadV1),
            )
            if (
                payload.environment_id != self.environment_id
                or payload.evidence_step_id != step.id
                or payload.frame_role != request.frame_role
                or payload.source_artifact_id != source_artifact.id
                or payload.source_sha256 != source_artifact.sha256
            ):
                raise ValueError("OCR extraction payload does not match canonical evidence")
            matches = [
                detection
                for detection in payload.detections
                if detection.metric_key == definition.metric_key
            ]
            if len(matches) != 1:
                raise ValueError("OCR metric extraction must contain exactly one matching value")
            detection = matches[0]
            if detection.confidence < definition.minimum_ocr_confidence:
                raise ValueError("OCR metric confidence is below its fixed threshold")
            region = detection.region
            if (
                region.x + region.width > step.viewport_width
                or region.y + region.height > step.viewport_height
            ):
                raise ValueError("OCR metric region is outside the source viewport")
            value = parse_metric_text(detection.text, definition.numeric_format)
            source: MetricObservationEvidenceV1 = ScreenshotOCRMetricEvidenceV1(
                frame_artifact_id=source_artifact.id,
                frame_sha256=source_artifact.sha256,
                extraction_artifact_id=extraction.id,
                extraction_sha256=extraction.sha256,
                region=region,
                raw_text=detection.text,
                confidence=detection.confidence,
                engine=payload.engine,
                engine_version=payload.engine_version,
            )
        else:
            if source_artifact.kind not in {"runtime_state", "source", "trace"}:
                raise ValueError("authoritative metric source has an unsupported kind")
            if source_artifact.id not in step.artifact_ids:
                raise ValueError("authoritative metric source is absent from its evidence step")
            payload = self._payload(
                source_artifact,
                TypeAdapter(AuthoritativeMetricSnapshotPayloadV1),
            )
            if (
                payload.environment_id != self.environment_id
                or payload.evidence_step_id != step.id
                or payload.frame_role != request.frame_role
            ):
                raise ValueError("authoritative metric payload does not match canonical evidence")
            matches = [
                metric for metric in payload.metrics if metric.metric_key == definition.metric_key
            ]
            if len(matches) != 1:
                raise ValueError(
                    "authoritative snapshot must contain exactly one matching metric"
                )
            match = matches[0]
            if match.unit != definition.unit:
                raise ValueError("authoritative metric unit does not match its definition")
            if _parse_timestamp(payload.captured_at, "authoritative capture time") != (
                _parse_timestamp(observed_at, "metric source capture time")
            ):
                raise ValueError("authoritative payload and artifact capture times differ")
            value = match.value
            source = AuthoritativeMetricEvidenceV1(
                snapshot_artifact_id=source_artifact.id,
                snapshot_sha256=source_artifact.sha256,
                provider=payload.provider,
                provider_version=payload.provider_version,
                record_locator=payload.record_locator,
            )

        self._range_check(definition, value)
        observation_payload = {
            "environment_id": self.environment_id,
            "definition": definition.model_dump(mode="json", by_alias=True),
            "evidence_step_id": step.id,
            "evidence_run_id": evidence_run.id,
            "frame_role": request.frame_role,
            "value": value,
            "observed_at": observed_at,
            "source": source.model_dump(mode="json"),
        }
        return AccountMetricObservationV1(
            id=_stable_id("account-metric-observation", observation_payload),
            **observation_payload,
        )

    def derive(
        self,
        definition: AccountMetricDefinitionV1,
        before_request: AccountMetricObservationRequestV1,
        after_request: AccountMetricObservationRequestV1,
    ) -> AccountMetricDeltaDerivationV1:
        if before_request.frame_role != "before" or after_request.frame_role != "after":
            raise ValueError("metric derivation requires explicit before and after observations")
        before = self.observe(definition, before_request)
        after = self.observe(definition, after_request)
        if _parse_timestamp(before.observed_at, "before observation time") >= _parse_timestamp(
            after.observed_at,
            "after observation time",
        ):
            raise ValueError("metric after observation must be newer than before observation")
        delta_value = after.value - before.value
        favorable = (
            delta_value > 0 and definition.improvement_direction == "higher_is_favorable"
        ) or (
            delta_value < 0 and definition.improvement_direction == "lower_is_favorable"
        )
        step_ids = list(dict.fromkeys((before.evidence_step_id, after.evidence_step_id)))
        artifact_ids = sorted(_observation_artifact_ids((before, after)))
        run_ids = list(dict.fromkeys((before.evidence_run_id, after.evidence_run_id)))
        delta = PlayerMetricDeltaV1(
            metric_key=definition.metric_key,
            label=definition.label,
            category=definition.category,
            before=before.value,
            after=after.value,
            delta=delta_value,
            unit=definition.unit,
            favorable=favorable,
            evidence_step_ids=step_ids,
        )
        reference = EvidenceReferenceV1(
            environment_id=self.environment_id,
            artifact_ids=artifact_ids,
            evidence_run_ids=run_ids,
            evidence_step_ids=step_ids,
            note="账号指标差值的前后观测、原图或权威快照与提取结果。",
        )
        identity_payload = {
            "environment_id": self.environment_id,
            "definition_id": definition.id,
            "before_observation_id": before.id,
            "after_observation_id": after.id,
            "delta": delta.model_dump(mode="json", by_alias=True),
        }
        return AccountMetricDeltaDerivationV1(
            id=_stable_id("account-metric-delta", identity_payload),
            environment_id=self.environment_id,
            evidence_refs=[reference],
            definition=definition,
            before_observation=before,
            after_observation=after,
            delta=delta,
            created_at=after.observed_at,
        )


SANGUO_ACCOUNT_POWER_DEFINITION = AccountMetricDefinitionV1(
    id="metric-definition.sanguo.account.power.v1",
    game_id="sanguo-mouding-tianxia",
    metric_key="sanguo.account.power",
    label="势力值",
    category="account_progression",
    unit="point",
    numeric_format="compact_zh",
    improvement_direction="higher_is_favorable",
    minimum_value=0,
    maximum_value=1_000_000_000,
)

AFK_ROWAN_LEVEL_DEFINITION = AccountMetricDefinitionV1(
    id="metric-definition.afk.rowan.level.v1",
    game_id="afk-journey",
    metric_key="afk.hero.rowan.level",
    label="罗万等级",
    category="account_progression",
    unit="level",
    numeric_format="integer",
    improvement_direction="higher_is_favorable",
    minimum_value=1,
    maximum_value=1_000,
)

_SANGUO_GAME_IDS = {
    "sanguo-mouding-tianxia",
    "sanguo-mou-ding-tian-xia",
    "nslg",
}
_AFK_GAME_IDS = {"afk-journey", "afkjourney"}


class CanonicalAccountMetricProvider:
    """Production reader for evidence already captured by the executor or game source."""

    def __init__(self, player_store: Any) -> None:
        self.player_store = player_store
        self.store: ObservatoryStore = player_store.observatory_store

    def _json_payload(self, artifact: ArtifactRef) -> dict[str, Any] | None:
        path = Path(artifact.path)
        if not path.is_absolute():
            path = self.store.root / path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _artifact_candidates(self, step: EvidenceStep) -> list[ArtifactRef]:
        candidates: list[ArtifactRef] = []
        for artifact_id in step.artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None:
                raise ValueError(f"metric provider artifact is missing: {artifact_id}")
            candidates.append(artifact)
        return candidates

    def _sanguo_derivations(
        self,
        environment_id: str,
        step: EvidenceStep,
    ) -> list[AccountMetricDeltaDerivationV1]:
        frames = {"before": step.before_frame_id, "after": step.after_frame_id}
        extractions: dict[str, list[str]] = {"before": [], "after": []}
        for artifact in self._artifact_candidates(step):
            payload = self._json_payload(artifact)
            if payload is None or payload.get("schema") != (
                "game-observatory.ai-player.metric-ocr-extraction.v1"
            ):
                continue
            parsed = MetricOCRExtractionPayloadV1.model_validate(payload)
            if parsed.environment_id != environment_id or parsed.evidence_step_id != step.id:
                raise ValueError("OCR metric extraction crosses canonical evidence")
            if parsed.source_artifact_id != frames[parsed.frame_role]:
                raise ValueError("OCR metric extraction names a non-canonical frame")
            if any(
                item.metric_key == SANGUO_ACCOUNT_POWER_DEFINITION.metric_key
                for item in parsed.detections
            ):
                extractions[parsed.frame_role].append(artifact.id)
        if any(len(extractions[role]) != 1 for role in ("before", "after")):
            return []
        observer = AccountMetricObserver(self.store, environment_id)
        return [
            observer.derive(
                SANGUO_ACCOUNT_POWER_DEFINITION,
                AccountMetricObservationRequestV1(
                    evidence_step_id=step.id,
                    frame_role="before",
                    source_kind="screenshot_ocr",
                    source_artifact_id=str(step.before_frame_id),
                    extraction_artifact_id=extractions["before"][0],
                ),
                AccountMetricObservationRequestV1(
                    evidence_step_id=step.id,
                    frame_role="after",
                    source_kind="screenshot_ocr",
                    source_artifact_id=str(step.after_frame_id),
                    extraction_artifact_id=extractions["after"][0],
                ),
            )
        ]

    def _afk_derivations(
        self,
        environment_id: str,
        step: EvidenceStep,
    ) -> list[AccountMetricDeltaDerivationV1]:
        snapshots: dict[str, list[str]] = {"before": [], "after": []}
        for artifact in self._artifact_candidates(step):
            payload = self._json_payload(artifact)
            if payload is None or payload.get("schema") != (
                "game-observatory.ai-player.authoritative-metric-snapshot.v1"
            ):
                continue
            parsed = AuthoritativeMetricSnapshotPayloadV1.model_validate(payload)
            if parsed.environment_id != environment_id or parsed.evidence_step_id != step.id:
                raise ValueError("authoritative metric snapshot crosses canonical evidence")
            if any(
                item.metric_key == AFK_ROWAN_LEVEL_DEFINITION.metric_key
                for item in parsed.metrics
            ):
                snapshots[parsed.frame_role].append(artifact.id)
        if any(len(snapshots[role]) != 1 for role in ("before", "after")):
            return []
        observer = AccountMetricObserver(self.store, environment_id)
        return [
            observer.derive(
                AFK_ROWAN_LEVEL_DEFINITION,
                AccountMetricObservationRequestV1(
                    evidence_step_id=step.id,
                    frame_role="before",
                    source_kind="authoritative_state",
                    source_artifact_id=snapshots["before"][0],
                ),
                AccountMetricObservationRequestV1(
                    evidence_step_id=step.id,
                    frame_role="after",
                    source_kind="authoritative_state",
                    source_artifact_id=snapshots["after"][0],
                ),
            )
        ]

    def derive_for_evidence_step(
        self,
        environment_id: str,
        evidence_step_id: str,
    ) -> list[AccountMetricDeltaDerivationV1]:
        """Derive every registered metric supported by one terminal evidence step.

        This entry point keeps the evidence reader independent of the autonomous
        orchestrator request type so external continuous-Agent actions can use the
        same hash-checked OCR/authoritative-state boundary.
        """

        environment = self.player_store.get_environment(environment_id)
        if environment is None:
            raise ValueError("metric provider environment is missing")
        step = self.store.get_evidence_step(evidence_step_id)
        if step is None or step.status != "passed" or not step.stability.settled:
            return []
        game_ids = {environment.game_id, *environment.game_id_aliases}
        if game_ids.intersection(_SANGUO_GAME_IDS):
            return self._sanguo_derivations(environment.id, step)
        if game_ids.intersection(_AFK_GAME_IDS):
            return self._afk_derivations(environment.id, step)
        return []

    def derive_account_metric_deltas(self, request: Any) -> list[AccountMetricDeltaDerivationV1]:
        return self.derive_for_evidence_step(
            request.command.environment_id,
            str(request.sample.evidence_step_id),
        )


def metric_delta_fingerprint(delta: PlayerMetricDeltaV1) -> str:
    """Stable lookup key used by storage to reject unregistered nested deltas."""

    return hashlib.sha256(
        _canonical_json(delta.model_dump(mode="json", by_alias=True))
    ).hexdigest()


def attach_account_metric_derivations(
    store: ObservatoryStore,
    sample: ActionQualitySampleV1,
    derivations: list[AccountMetricDeltaDerivationV1],
) -> ActionQualitySampleV1:
    """Attach only recomputed metric facts to a canonical executed quality sample."""

    if not derivations:
        return sample
    if sample.account_metric_deltas:
        raise ValueError("account metric deltas must be attached through one canonical batch")
    if sample.execution_disposition != "executed" or sample.outcome != "confirmed":
        raise ValueError("account metric progress requires a confirmed executed action")
    if sample.evidence_step_id is None:
        raise ValueError("account metric progress requires a terminal evidence step")
    metric_keys = [item.definition.metric_key for item in derivations]
    if len(metric_keys) != len(set(metric_keys)):
        raise ValueError("account metric derivations must use unique metric keys")
    for derivation in derivations:
        if derivation.environment_id != sample.environment_id:
            raise ValueError("account metric derivation belongs to another environment")
        if derivation.after_observation.evidence_step_id != sample.evidence_step_id:
            raise ValueError("account metric after observation is not the action terminal")
        validate_account_metric_derivation(store, derivation)

    references = [
        *sample.evidence_refs,
        *(reference for item in derivations for reference in item.evidence_refs),
    ]
    merged = EvidenceReferenceV1(
        environment_id=sample.environment_id,
        artifact_ids=list(
            dict.fromkeys(
                artifact_id
                for reference in references
                for artifact_id in reference.artifact_ids
            )
        ),
        evidence_run_ids=list(
            dict.fromkeys(
                run_id
                for reference in references
                for run_id in reference.evidence_run_ids
            )
        ),
        evidence_step_ids=list(
            dict.fromkeys(
                step_id
                for reference in references
                for step_id in reference.evidence_step_ids
            )
        ),
        trace_run_ids=list(
            dict.fromkeys(
                run_id for reference in references for run_id in reference.trace_run_ids
            )
        ),
        source_ids=list(
            dict.fromkeys(
                source_id for reference in references for source_id in reference.source_ids
            )
        ),
        note="动作质量样本及其账号指标前后观测的合并证据。",
    )
    payload = sample.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "evidence_refs": [merged.model_dump(mode="json", by_alias=True)],
            "account_metric_deltas": [
                item.delta.model_dump(mode="json", by_alias=True) for item in derivations
            ],
        }
    )
    return ActionQualitySampleV1.model_validate(payload)


def validate_account_metric_derivation(
    store: ObservatoryStore,
    derivation: AccountMetricDeltaDerivationV1,
) -> AccountMetricDeltaDerivationV1:
    """Recompute a portable derivation from canonical storage and reject any drift."""

    observer = AccountMetricObserver(store, derivation.environment_id)
    before_source = derivation.before_observation.source
    after_source = derivation.after_observation.source

    def request(
        observation: AccountMetricObservationV1,
        source: MetricObservationEvidenceV1,
    ) -> AccountMetricObservationRequestV1:
        if isinstance(source, ScreenshotOCRMetricEvidenceV1):
            return AccountMetricObservationRequestV1(
                evidence_step_id=observation.evidence_step_id,
                frame_role=observation.frame_role,
                source_kind="screenshot_ocr",
                source_artifact_id=source.frame_artifact_id,
                extraction_artifact_id=source.extraction_artifact_id,
            )
        return AccountMetricObservationRequestV1(
            evidence_step_id=observation.evidence_step_id,
            frame_role=observation.frame_role,
            source_kind="authoritative_state",
            source_artifact_id=source.snapshot_artifact_id,
        )

    recomputed = observer.derive(
        derivation.definition,
        request(derivation.before_observation, before_source),
        request(derivation.after_observation, after_source),
    )
    expected = derivation.model_copy(update={"created_at": recomputed.created_at})
    if recomputed != expected:
        raise ValueError("account metric derivation differs from canonical evidence")
    return derivation


__all__ = [
    "AFK_ROWAN_LEVEL_DEFINITION",
    "AccountMetricDefinitionV1",
    "AccountMetricDeltaDerivationV1",
    "AccountMetricObservationRequestV1",
    "AccountMetricObservationV1",
    "AccountMetricObserver",
    "AuthoritativeMetricSnapshotPayloadV1",
    "CanonicalAccountMetricProvider",
    "MetricOCRExtractionPayloadV1",
    "SANGUO_ACCOUNT_POWER_DEFINITION",
    "attach_account_metric_derivations",
    "metric_delta_fingerprint",
    "parse_metric_text",
    "validate_account_metric_derivation",
]
