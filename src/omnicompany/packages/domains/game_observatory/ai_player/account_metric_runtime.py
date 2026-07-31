"""Selective, evidence-bound account metric extraction for completed game actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..models import ArtifactRef, SourcePixelRect
from ..recognition_service import RecognitionService
from ..store import ObservatoryStore
from .account_metric_observation import (
    AccountMetricDefinitionV1,
    AccountMetricDeltaDerivationV1,
    AccountMetricObservationRequestV1,
    AccountMetricObserver,
    MetricOCRDetectionV1,
    MetricOCRExtractionPayloadV1,
    parse_metric_text,
)
from .store import AIPlayerStore


RegionOCR = Callable[[Path, SourcePixelRect], tuple[str, float, str, str]]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _verified_artifact_path(store: ObservatoryStore, artifact: ArtifactRef) -> Path:
    path = Path(artifact.path)
    if not path.is_absolute():
        path = store.root / path
    path = path.resolve()
    try:
        path.relative_to(store.root.resolve())
    except ValueError as error:
        raise ValueError(f"metric source path escapes canonical root: {artifact.id}") from error
    if not path.is_file():
        raise ValueError(f"metric source artifact is missing: {artifact.id}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ValueError(f"metric source artifact hash changed: {artifact.id}")
    return path


def _rapidocr_region(
    frame_path: Path,
    region: SourcePixelRect,
) -> tuple[str, float, str, str]:
    result = RecognitionService().ocr_region(frame_path, region)
    if len(result.detections) != 1:
        raise ValueError(
            "selective metric OCR must return exactly one text candidate in the declared region"
        )
    detection = result.detections[0]
    return (
        str(detection.text),
        detection.score,
        result.backend,
        result.backend_version,
    )


def _metric_extraction_artifact(
    store: ObservatoryStore,
    *,
    environment_id: str,
    evidence_step_id: str,
    frame_role: str,
    definition: AccountMetricDefinitionV1,
    region: SourcePixelRect,
    extractor: RegionOCR,
) -> ArtifactRef:
    step = store.get_evidence_step(evidence_step_id)
    if step is None:
        raise ValueError(f"metric evidence step is missing: {evidence_step_id}")
    run = store.get_evidence_run(step.evidence_run_id)
    if run is None or run.status != "passed" or step.status != "passed" or not step.ended_at:
        raise ValueError("metric extraction requires a terminal-passed EvidenceStep")
    if run.environment.get("environment_id") != environment_id:
        raise ValueError("metric extraction evidence belongs to another environment")
    frame_id = step.before_frame_id if frame_role == "before" else step.after_frame_id
    if frame_id is None:
        raise ValueError(f"metric EvidenceStep lacks its {frame_role} frame")
    frame = store.get_artifact(frame_id)
    if frame is None or frame.kind != "screenshot":
        raise ValueError(f"metric {frame_role} source is not a canonical screenshot")
    if frame.metadata.get("environment_id") != environment_id:
        raise ValueError("metric source screenshot belongs to another environment")
    frame_path = _verified_artifact_path(store, frame)
    text, confidence, engine, engine_version = extractor(frame_path, region)
    if confidence < definition.minimum_ocr_confidence:
        raise ValueError("metric OCR confidence is below the fixed definition threshold")
    parse_metric_text(text, definition.numeric_format)
    payload = MetricOCRExtractionPayloadV1(
        environment_id=environment_id,
        evidence_step_id=step.id,
        frame_role=frame_role,
        source_artifact_id=frame.id,
        source_sha256=frame.sha256,
        engine=engine,
        engine_version=engine_version,
        detections=[
            MetricOCRDetectionV1(
                metric_key=definition.metric_key,
                region=region,
                text=text,
                confidence=confidence,
            )
        ],
    )
    content = _canonical_json(payload.model_dump(mode="json", by_alias=True)) + b"\n"
    identity = {
        "environment_id": environment_id,
        "evidence_step_id": step.id,
        "frame_role": frame_role,
        "definition_id": definition.id,
        "source_sha256": frame.sha256,
        "region": region.model_dump(mode="json"),
        "engine": engine,
        "engine_version": engine_version,
    }
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    artifact_id = f"artifact.account-metric-extraction.{digest[:32]}"
    path = store.artifact_root / "account_metric_extractions" / f"{digest}.json"
    existing = store.get_artifact(artifact_id)
    content_sha256 = hashlib.sha256(content).hexdigest()
    if existing is not None:
        existing_path = _verified_artifact_path(store, existing)
        if existing.sha256 != content_sha256 or existing_path.read_bytes() != content:
            raise ValueError(
                "metric extraction identity already has different OCR output; review the region"
            )
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise ValueError("metric extraction path already contains different content")
    if not path.exists():
        path.write_bytes(content)
    artifact = ArtifactRef(
        id=artifact_id,
        kind="trace",
        path=str(path),
        sha256=content_sha256,
        captured_at=frame.captured_at,
        run_id=run.id,
        media_type="application/json",
        metadata={
            "environment_id": environment_id,
            "evidence_run_id": run.id,
            "evidence_step_id": step.id,
            "evidence_role": frame_role,
            "source_artifact_id": frame.id,
            "metric_definition_id": definition.id,
            "selective_region_only": True,
        },
    )
    store.save_artifact(artifact)
    return artifact


def extract_and_persist_screenshot_metric(
    player: AIPlayerStore,
    *,
    environment_id: str,
    evidence_step_id: str,
    definition: AccountMetricDefinitionV1,
    before_region: SourcePixelRect,
    after_region: SourcePixelRect,
    extractor: RegionOCR | None = None,
) -> tuple[AccountMetricDeltaDerivationV1, tuple[ArtifactRef, ArtifactRef], bool]:
    """OCR two declared regions, derive one delta, and persist it idempotently."""

    environment = player.get_environment(environment_id)
    if environment is None:
        raise ValueError(f"metric environment is missing: {environment_id}")
    accepted_game_ids = {environment.game_id, *environment.game_id_aliases}
    if definition.game_id not in accepted_game_ids:
        raise ValueError("metric definition game_id is outside the environment identity")
    region_ocr = extractor or _rapidocr_region
    before = _metric_extraction_artifact(
        player.observatory_store,
        environment_id=environment_id,
        evidence_step_id=evidence_step_id,
        frame_role="before",
        definition=definition,
        region=before_region,
        extractor=region_ocr,
    )
    after = _metric_extraction_artifact(
        player.observatory_store,
        environment_id=environment_id,
        evidence_step_id=evidence_step_id,
        frame_role="after",
        definition=definition,
        region=after_region,
        extractor=region_ocr,
    )
    step = player.observatory_store.get_evidence_step(evidence_step_id)
    if step is None:
        raise ValueError(f"metric evidence step is missing: {evidence_step_id}")
    observer = AccountMetricObserver(player.observatory_store, environment_id)
    derivation = observer.derive(
        definition,
        AccountMetricObservationRequestV1(
            evidence_step_id=step.id,
            frame_role="before",
            source_kind="screenshot_ocr",
            source_artifact_id=str(step.before_frame_id),
            extraction_artifact_id=before.id,
        ),
        AccountMetricObservationRequestV1(
            evidence_step_id=step.id,
            frame_role="after",
            source_kind="screenshot_ocr",
            source_artifact_id=str(step.after_frame_id),
            extraction_artifact_id=after.id,
        ),
    )
    existing = player.get_account_metric_derivation(environment_id, derivation.id)
    if existing is not None:
        if existing != derivation:
            raise ValueError(f"account metric derivation conflicts: {derivation.id}")
        return existing, (before, after), False
    player.append_account_metric_derivation(derivation)
    return derivation, (before, after), True


def persist_authoritative_metric(
    player: AIPlayerStore,
    *,
    environment_id: str,
    evidence_step_id: str,
    definition: AccountMetricDefinitionV1,
    before_artifact_id: str,
    after_artifact_id: str,
) -> tuple[AccountMetricDeltaDerivationV1, bool]:
    """Persist one delta from two already-captured authoritative state snapshots."""

    environment = player.get_environment(environment_id)
    if environment is None:
        raise ValueError(f"metric environment is missing: {environment_id}")
    if definition.game_id not in {environment.game_id, *environment.game_id_aliases}:
        raise ValueError("metric definition game_id is outside the environment identity")
    observer = AccountMetricObserver(player.observatory_store, environment_id)
    derivation = observer.derive(
        definition,
        AccountMetricObservationRequestV1(
            evidence_step_id=evidence_step_id,
            frame_role="before",
            source_kind="authoritative_state",
            source_artifact_id=before_artifact_id,
        ),
        AccountMetricObservationRequestV1(
            evidence_step_id=evidence_step_id,
            frame_role="after",
            source_kind="authoritative_state",
            source_artifact_id=after_artifact_id,
        ),
    )
    existing = player.get_account_metric_derivation(environment_id, derivation.id)
    if existing is not None:
        if existing != derivation:
            raise ValueError(f"account metric derivation conflicts: {derivation.id}")
        return existing, False
    player.append_account_metric_derivation(derivation)
    return derivation, True


__all__ = [
    "RegionOCR",
    "extract_and_persist_screenshot_metric",
    "persist_authoritative_metric",
]
