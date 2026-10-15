# [OMNI] origin=codex created_by=codex intent="collect hash-bound training planner measurements without inventing usage"
"""Offline collection of planner measurements from immutable worker attempts.

The future runner integration writes one manifest inside every unique worker
run directory after that attempt finishes.  This module verifies the manifest,
the full result bytes, and an optional event-log copy before reading OpenCode
``step_finish`` events.  It deliberately does not read the mutable convenience
``worker-result.json`` file.

Raw ``step_finish`` usage remains useful even when it cannot become a canonical
``PlannerMeasurementReceiptV1``.  A receipt is exposed only when the producer
event already carries a complete, attested ``planner_measurement`` envelope and
the caller supplies the configured planner trust store.  The collector never
creates an attestation and never treats a worker resume as a model invocation.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..ai_player.planner_measurement import (
    PlannerMeasurementReceiptV1,
    PlannerMeasurementTrustStore,
)


ATTEMPT_MANIFEST_SCHEMA = (
    "omnicompany.arknights.training-planner-worker-attempt.v1"
)
COLLECTION_SCHEMA = "omnicompany.arknights.training-planner-measurements.v1"
INVOCATION_SCHEMA = "omnicompany.arknights.training-planner-invocation.v1"
PLANNER_BINDING_SCHEMA = "omnicompany.arknights.training-planner-binding.v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class HashedFileV1(_StrictModel):
    path: str = Field(min_length=1)
    bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkerAttemptManifestRefV1(_StrictModel):
    """Caller-pinned bytes for one immutable attempt manifest."""

    path: str = Field(min_length=1)
    bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkerAttemptManifestV1(_StrictModel):
    """Runner-authored binding for one unique external-worker result."""

    schema_id: str = Field(default=ATTEMPT_MANIFEST_SCHEMA, alias="schema")
    attempt_id: str = Field(min_length=1)
    worker_attempt: int = Field(ge=1)
    worker_run_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    ai_player_session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    provider_session_id: str | None = Field(default=None, min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    result_artifact: HashedFileV1
    event_log_artifact: HashedFileV1 | None = None

    @model_validator(mode="after")
    def validate_attempt_window(self) -> "WorkerAttemptManifestV1":
        started = _timestamp(self.started_at, "attempt started_at")
        completed = _timestamp(self.completed_at, "attempt completed_at")
        if completed < started:
            raise ValueError("attempt completed_at precedes started_at")
        return self


class NullableUsageV1(_StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)


class PlannerBindingV1(_StrictModel):
    """Complete producer envelope required before exposing a receipt."""

    schema_id: str = Field(default=PLANNER_BINDING_SCHEMA, alias="schema")
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    planner_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    decision_latency_ms: int = Field(ge=0)
    completed_at: str = Field(min_length=1)
    receipt: PlannerMeasurementReceiptV1


class TrainingPlannerInvocationV1(_StrictModel):
    schema_id: str = Field(default=INVOCATION_SCHEMA, alias="schema")
    attempt_id: str = Field(min_length=1)
    worker_attempt: int = Field(ge=1)
    worker_run_id: str = Field(min_length=1)
    provider_session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    wall_latency_ms: int | None = Field(default=None, ge=0)
    decision_latency_ms: int | None = Field(default=None, ge=0)
    usage: NullableUsageV1
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_result_path: str = Field(min_length=1)
    source_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_log_path: str | None = Field(default=None, min_length=1)
    source_event_log_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    source_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt: PlannerMeasurementReceiptV1 | None = None
    receipt_unavailable_reasons: list[str] = Field(default_factory=list)


class CoverageV1(_StrictModel):
    invocation_count: int | None = Field(default=None, ge=0)
    measured_counts: dict[str, int]
    measured_rates: dict[str, float | None]
    completely_unavailable_invocations: int = Field(ge=0)
    receipt_count: int = Field(ge=0)


class LatencySummaryV1(_StrictModel):
    wall_measured_count: int = Field(ge=0)
    wall_observed_sum_ms: int | None = Field(default=None, ge=0)
    decision_measured_count: int = Field(ge=0)
    decision_observed_sum_ms: int | None = Field(default=None, ge=0)


class ReceiptLinkV1(_StrictModel):
    invocation_id: str = Field(min_length=1)
    receipt_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)


class TrainingPlannerMeasurementCollectionV1(_StrictModel):
    schema_id: str = Field(default=COLLECTION_SCHEMA, alias="schema")
    attempt_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    ai_player_session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    worker_attempt_count: int = Field(ge=1)
    actual_invocation_count: int | None = Field(default=None, ge=0)
    count_status: Literal["measured", "unavailable"]
    invocations: list[TrainingPlannerInvocationV1]
    observed_usage_sums: NullableUsageV1
    usage_coverage: CoverageV1
    latency: LatencySummaryV1
    receipt_links: list[ReceiptLinkV1]

    @model_validator(mode="after")
    def validate_count_and_coverage(self) -> "TrainingPlannerMeasurementCollectionV1":
        observed = len(self.invocations)
        if self.count_status == "measured" and self.actual_invocation_count != observed:
            raise ValueError("measured invocation count differs from collected invocations")
        if self.count_status == "unavailable" and self.actual_invocation_count is not None:
            raise ValueError("unavailable invocation count must remain null")
        if self.usage_coverage.invocation_count != self.actual_invocation_count:
            raise ValueError("usage coverage count differs from invocation count status")
        if self.usage_coverage.receipt_count != len(self.receipt_links):
            raise ValueError("receipt coverage differs from receipt links")
        invocation_ids = {value.invocation_id for value in self.invocations}
        if any(
            item.invocation_id not in invocation_ids for item in self.receipt_links
        ):
            raise ValueError("receipt link references an absent invocation")
        return self


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _event_time(value: Any, label: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} requires a numeric producer timestamp")
    if not math.isfinite(float(value)):
        raise ValueError(f"{label} timestamp is not finite")
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"{label} timestamp is outside the supported range") from error


def _read_hashed_file(value: HashedFileV1, label: str) -> tuple[Path, bytes]:
    supplied = Path(value.path)
    if not supplied.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    path = supplied.resolve()
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    raw = path.read_bytes()
    if _digest(raw) != value.bytes_sha256:
        raise ValueError(f"{label} bytes hash mismatch: {path}")
    return path, raw


def _read_manifest(
    reference: WorkerAttemptManifestRefV1,
) -> tuple[Path, str, WorkerAttemptManifestV1]:
    manifest_file = HashedFileV1(
        path=reference.path,
        bytes_sha256=reference.bytes_sha256,
    )
    path, raw = _read_hashed_file(manifest_file, "worker attempt manifest")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("worker attempt manifest is not UTF-8 JSON") from error
    manifest = WorkerAttemptManifestV1.model_validate(payload)
    if manifest.schema_id != ATTEMPT_MANIFEST_SCHEMA:
        raise ValueError("worker attempt manifest schema is unsupported")
    if path.parent.name != manifest.worker_run_id:
        raise ValueError("worker attempt manifest is not inside its unique worker run directory")
    return path, reference.bytes_sha256, manifest


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _event_rows(raw: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"worker event log contains a blank row at {line_number}")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"worker event log row {line_number} is invalid JSON") from error
        if not isinstance(row, dict):
            raise ValueError(f"worker event log row {line_number} is not an object")
        rows.append(row)
    return rows


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer or null")
    return value


def _optional_nonnegative_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a non-negative number or null")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return number


def _usage(part: Mapping[str, Any]) -> NullableUsageV1:
    raw_tokens = part.get("tokens")
    if raw_tokens is not None and not isinstance(raw_tokens, Mapping):
        raise ValueError("step_finish tokens must be an object")
    tokens = raw_tokens if isinstance(raw_tokens, Mapping) else {}
    raw_cache = tokens.get("cache")
    if raw_cache is not None and not isinstance(raw_cache, Mapping):
        raise ValueError("step_finish cache tokens must be an object")
    cache = raw_cache if isinstance(raw_cache, Mapping) else {}
    return NullableUsageV1(
        input_tokens=_optional_nonnegative_int(
            tokens.get("input"), "step_finish input tokens"
        ),
        output_tokens=_optional_nonnegative_int(
            tokens.get("output"), "step_finish output tokens"
        ),
        cached_input_tokens=_optional_nonnegative_int(
            cache.get("read", tokens.get("cache_read")),
            "step_finish cache-read tokens",
        ),
        cache_creation_input_tokens=_optional_nonnegative_int(
            cache.get("write", tokens.get("cache_write")),
            "step_finish cache-write tokens",
        ),
        reasoning_tokens=_optional_nonnegative_int(
            tokens.get("reasoning"), "step_finish reasoning tokens"
        ),
        cost=_optional_nonnegative_number(part.get("cost"), "step_finish cost"),
    )


def _event_identity(payload: Mapping[str, Any], part: Mapping[str, Any]) -> str:
    return str(payload.get("id") or payload.get("eventID") or part.get("id") or "").strip()


def _step_key(part: Mapping[str, Any]) -> str:
    return str(part.get("messageID") or part.get("stepID") or "").strip()


def _explicit_value(
    payload: Mapping[str, Any],
    part: Mapping[str, Any],
    *names: str,
) -> str | None:
    for source in (part, payload):
        for name in names:
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _validate_result(
    manifest_path: Path,
    manifest: WorkerAttemptManifestV1,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], Path | None, str | None]:
    result_path, result_raw = _read_hashed_file(
        manifest.result_artifact,
        "worker result artifact",
    )
    if result_path.parent != manifest_path.parent:
        raise ValueError("worker result is not isolated in its manifest run directory")
    result = _json_object(result_raw, "worker result artifact")
    if result.get("run_id") != manifest.worker_run_id:
        raise ValueError("worker result run_id differs from the attempt manifest")
    if result.get("provider") != manifest.provider:
        raise ValueError("worker result provider differs from the attempt manifest")
    run_record_dir = result.get("run_record_dir")
    if run_record_dir is not None and Path(str(run_record_dir)).resolve() != manifest_path.parent:
        raise ValueError("worker result run_record_dir differs from its manifest directory")
    raw = result.get("raw")
    if not isinstance(raw, Mapping):
        raise ValueError("worker result raw payload is missing")
    result_session = str(raw.get("session_id") or "").strip() or None
    if result_session != manifest.provider_session_id:
        raise ValueError("worker result provider session differs from the attempt manifest")
    asserted_model = result.get("model") or raw.get("model")
    if asserted_model is not None and str(asserted_model).strip() != manifest.model:
        raise ValueError("worker result model differs from the attempt manifest")
    duration_ms = _optional_nonnegative_number(
        result.get("duration_ms"), "worker result duration_ms"
    )
    if duration_ms is None:
        raise ValueError("worker result duration_ms is missing")
    window_ms = (
        _timestamp(manifest.completed_at, "attempt completed_at")
        - _timestamp(manifest.started_at, "attempt started_at")
    ).total_seconds() * 1000
    if duration_ms > window_ms + 1000:
        raise ValueError("worker result duration exceeds the hash-bound attempt window")
    embedded = result.get("events")
    if not isinstance(embedded, list) or any(not isinstance(row, dict) for row in embedded):
        raise ValueError("worker result events must be a list of objects")
    events = [dict(row) for row in embedded]
    event_log_path: Path | None = None
    event_log_sha256: str | None = None
    if manifest.event_log_artifact is not None:
        event_log_path, event_raw = _read_hashed_file(
            manifest.event_log_artifact,
            "worker event log artifact",
        )
        if event_log_path.parent != manifest_path.parent:
            raise ValueError("worker event log is not isolated in its manifest run directory")
        event_rows = _event_rows(event_raw)
        if event_rows != events:
            raise ValueError("worker event log differs from the result event sequence")
        event_log_sha256 = manifest.event_log_artifact.bytes_sha256
    return result_path, result, events, event_log_path, event_log_sha256


def _validated_receipt(
    raw_binding: Any,
    *,
    manifest: WorkerAttemptManifestV1,
    provider_invocation_id: str,
    usage: NullableUsageV1,
    event_completed_at: datetime,
    trust_store: PlannerMeasurementTrustStore | None,
) -> tuple[PlannerMeasurementReceiptV1 | None, int | None, list[str]]:
    if raw_binding is None:
        return None, None, ["trusted planner binding unavailable"]
    binding = PlannerBindingV1.model_validate(raw_binding)
    if binding.schema_id != PLANNER_BINDING_SCHEMA:
        raise ValueError("planner binding schema is unsupported")
    expected = (
        binding.environment_id,
        binding.session_id,
        binding.task_id,
        binding.provider,
        binding.model,
    )
    actual = (
        manifest.environment_id,
        manifest.ai_player_session_id,
        manifest.task_id,
        manifest.provider,
        manifest.model,
    )
    if expected != actual:
        raise ValueError("planner binding crosses the worker attempt scope")
    receipt = binding.receipt
    receipt_binding = (
        receipt.environment_id,
        receipt.session_id,
        receipt.task_id,
        receipt.command_id,
        receipt.planner_request_sha256,
        receipt.provider,
        receipt.model,
        receipt.invocation_id,
        receipt.decision_latency_ms,
        receipt.completed_at,
    )
    explicit_binding = (
        binding.environment_id,
        binding.session_id,
        binding.task_id,
        binding.command_id,
        binding.planner_request_sha256,
        binding.provider,
        binding.model,
        binding.invocation_id,
        binding.decision_latency_ms,
        binding.completed_at,
    )
    if receipt_binding != explicit_binding:
        raise ValueError("planner receipt differs from its producer binding")
    if binding.invocation_id != provider_invocation_id:
        raise ValueError("planner receipt invocation differs from step_finish")
    if (
        receipt.model_input_tokens != usage.input_tokens
        or receipt.model_output_tokens != usage.output_tokens
    ):
        raise ValueError("planner receipt tokens differ from step_finish usage")
    completed = _timestamp(binding.completed_at, "planner binding completed_at")
    if abs((completed - event_completed_at).total_seconds()) > 5:
        raise ValueError("planner receipt completion time differs from step_finish")
    if receipt.attestation is None:
        return None, binding.decision_latency_ms, ["planner attestation unavailable"]
    if trust_store is None or not trust_store.configured:
        return None, binding.decision_latency_ms, ["planner trust root unavailable"]
    try:
        trust_store.verify(receipt)
    except ValueError as error:
        raise ValueError("planner receipt attestation is invalid") from error
    return receipt, binding.decision_latency_ms, []


def _manifest_invocations(
    manifest_path: Path,
    manifest_sha256: str,
    manifest: WorkerAttemptManifestV1,
    *,
    trust_store: PlannerMeasurementTrustStore | None,
) -> tuple[list[TrainingPlannerInvocationV1], bool]:
    (
        result_path,
        result,
        events,
        event_log_path,
        event_log_sha256,
    ) = _validate_result(manifest_path, manifest)
    provider_result_available = result.get("status") != "unavailable"
    if not provider_result_available and (
        manifest.provider_session_id is not None
        or events
        or not str(result.get("measurement_unavailable_reason") or "").strip()
    ):
        raise ValueError("unavailable worker result is not an explicit missing-result artifact")
    started = _timestamp(manifest.started_at, "attempt started_at")
    completed = _timestamp(manifest.completed_at, "attempt completed_at")
    step_starts: dict[str, datetime] = {}
    invocations: list[TrainingPlannerInvocationV1] = []
    last_event_at: datetime | None = None
    for event_index, event in enumerate(events, 1):
        event_type = str(event.get("type") or "").strip()
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        provider_type = str(payload.get("type") or "").strip()
        if event_type not in {"step_start", "step_finish"} and provider_type not in {
            "step_start",
            "step_finish",
        }:
            continue
        if event_type and provider_type and event_type != provider_type:
            raise ValueError(f"worker event {event_index} type contradicts its payload")
        event_at = _event_time(event.get("ts"), f"worker event {event_index}")
        if event_at < started or event_at > completed:
            raise ValueError(f"worker event {event_index} falls outside its attempt window")
        if last_event_at is not None and event_at < last_event_at:
            raise ValueError("worker step event timestamps are not monotonic")
        last_event_at = event_at
        part = payload.get("part")
        if not isinstance(part, Mapping):
            raise ValueError(f"worker step event {event_index} has no part object")
        provider_session = str(payload.get("sessionID") or "").strip()
        if not provider_session or provider_session != manifest.provider_session_id:
            raise ValueError(f"worker event {event_index} crosses its provider session")
        asserted_provider = _explicit_value(payload, part, "provider", "providerID")
        asserted_model = _explicit_value(payload, part, "model", "modelID")
        if asserted_provider is not None and asserted_provider != manifest.provider:
            raise ValueError(f"worker event {event_index} crosses its provider")
        if asserted_model is not None and asserted_model != manifest.model:
            raise ValueError(f"worker event {event_index} crosses its model")
        pair_key = _step_key(part)
        kind = provider_type or event_type
        if kind == "step_start":
            if pair_key:
                if pair_key in step_starts:
                    raise ValueError("duplicate step_start identity in one worker attempt")
                step_starts[pair_key] = event_at
            continue

        event_id = _event_identity(payload, part)
        if not event_id:
            raise ValueError("step_finish lacks a stable producer event id")
        invocation_id = f"{provider_session}:{event_id}"
        usage = _usage(part)
        wall_latency_ms: int | None = None
        if pair_key and pair_key in step_starts:
            wall_latency_ms = round(
                (event_at - step_starts.pop(pair_key)).total_seconds() * 1000
            )
            if wall_latency_ms < 0:
                raise ValueError("step_finish precedes its step_start")
        binding = part.get("planner_measurement", payload.get("planner_measurement"))
        receipt, decision_latency_ms, unavailable = _validated_receipt(
            binding,
            manifest=manifest,
            provider_invocation_id=invocation_id,
            usage=usage,
            event_completed_at=event_at,
            trust_store=trust_store,
        )
        explicit_latency = part.get("decision_latency_ms")
        if explicit_latency is not None:
            measured_latency = _optional_nonnegative_int(
                explicit_latency,
                "step_finish decision_latency_ms",
            )
            if decision_latency_ms is not None and measured_latency != decision_latency_ms:
                raise ValueError("step_finish decision latency contradicts planner binding")
            decision_latency_ms = measured_latency
        invocations.append(
            TrainingPlannerInvocationV1(
                attempt_id=manifest.attempt_id,
                worker_attempt=manifest.worker_attempt,
                worker_run_id=manifest.worker_run_id,
                provider_session_id=provider_session,
                provider=manifest.provider,
                model=manifest.model,
                invocation_id=invocation_id,
                event_id=event_id,
                completed_at=event_at.isoformat().replace("+00:00", "Z"),
                wall_latency_ms=wall_latency_ms,
                decision_latency_ms=decision_latency_ms,
                usage=usage,
                source_manifest_path=str(manifest_path),
                source_manifest_sha256=manifest_sha256,
                source_result_path=str(result_path),
                source_result_sha256=manifest.result_artifact.bytes_sha256,
                source_event_log_path=(
                    str(event_log_path) if event_log_path is not None else None
                ),
                source_event_log_sha256=event_log_sha256,
                source_event_sha256=_digest(_canonical(event)),
                receipt=receipt,
                receipt_unavailable_reasons=unavailable,
            )
        )
    return invocations, provider_result_available


def _observed_sums(
    invocations: Sequence[TrainingPlannerInvocationV1],
) -> NullableUsageV1:
    fields = tuple(NullableUsageV1.model_fields)
    values: dict[str, int | float | None] = {}
    for field in fields:
        measured = [
            getattr(invocation.usage, field)
            for invocation in invocations
            if getattr(invocation.usage, field) is not None
        ]
        values[field] = sum(measured) if measured else None
    return NullableUsageV1.model_validate(values)


def collect_training_planner_measurements(
    manifest_references: Sequence[WorkerAttemptManifestRefV1 | Mapping[str, Any]],
    *,
    trust_store: PlannerMeasurementTrustStore | None = None,
) -> TrainingPlannerMeasurementCollectionV1:
    """Collect actual provider invocations across immutable worker attempts.

    ``actual_invocation_count`` is the number of unique, validated
    ``step_finish`` events.  It is intentionally unrelated to the number of
    manifests or worker resumes.
    """

    if not manifest_references:
        raise ValueError("at least one worker attempt manifest is required")
    loaded = [
        _read_manifest(WorkerAttemptManifestRefV1.model_validate(reference))
        for reference in manifest_references
    ]
    manifests = [item[2] for item in loaded]
    scopes = {
        (
            item.attempt_id,
            item.environment_id,
            item.ai_player_session_id,
            item.task_id,
            item.provider,
            item.model,
        )
        for item in manifests
    }
    if len(scopes) != 1:
        raise ValueError("worker attempt manifests cross one training planner scope")
    ordinals = sorted(item.worker_attempt for item in manifests)
    if ordinals != list(range(1, len(manifests) + 1)):
        raise ValueError("worker attempt ordinals must be unique and contiguous")
    worker_run_ids = [item.worker_run_id for item in manifests]
    manifest_paths = [str(item[0]) for item in loaded]
    result_paths = [str(Path(item.result_artifact.path).resolve()) for item in manifests]
    result_hashes = [item.result_artifact.bytes_sha256 for item in manifests]
    if any(
        len(values) != len(set(values))
        for values in (worker_run_ids, manifest_paths, result_paths, result_hashes)
    ):
        raise ValueError("worker attempts reuse a run id or immutable artifact")

    invocations: list[TrainingPlannerInvocationV1] = []
    provider_results_available = True
    for manifest_path, manifest_sha256, manifest in sorted(
        loaded,
        key=lambda item: item[2].worker_attempt,
    ):
        attempt_invocations, attempt_result_available = _manifest_invocations(
            manifest_path,
            manifest_sha256,
            manifest,
            trust_store=trust_store,
        )
        invocations.extend(attempt_invocations)
        provider_results_available = (
            provider_results_available and attempt_result_available
        )
    invocation_ids = [item.invocation_id for item in invocations]
    event_hashes = [item.source_event_sha256 for item in invocations]
    if len(invocation_ids) != len(set(invocation_ids)):
        raise ValueError("duplicate provider invocation appears across worker attempts")
    if len(event_hashes) != len(set(event_hashes)):
        raise ValueError("duplicate step_finish event appears across worker attempts")

    usage_fields = tuple(NullableUsageV1.model_fields)
    measured_counts = {
        field: sum(getattr(item.usage, field) is not None for item in invocations)
        for field in usage_fields
    }
    observed_count = len(invocations)
    count = observed_count if provider_results_available else None
    measured_rates = {
        field: (measured / observed_count if observed_count else None)
        for field, measured in measured_counts.items()
    }
    unavailable_count = sum(
        all(getattr(item.usage, field) is None for field in usage_fields)
        for item in invocations
    )
    wall_values = [
        item.wall_latency_ms
        for item in invocations
        if item.wall_latency_ms is not None
    ]
    decision_values = [
        item.decision_latency_ms
        for item in invocations
        if item.decision_latency_ms is not None
    ]
    receipt_links = [
        ReceiptLinkV1(
            invocation_id=item.invocation_id,
            receipt_id=item.receipt.id,
            artifact_id=item.receipt.artifact_id,
        )
        for item in invocations
        if item.receipt is not None
    ]
    if len({item.receipt_id for item in receipt_links}) != len(receipt_links) or len(
        {item.artifact_id for item in receipt_links}
    ) != len(receipt_links):
        raise ValueError("planner receipt or artifact identity is reused")
    scope = next(iter(scopes))
    return TrainingPlannerMeasurementCollectionV1(
        attempt_id=scope[0],
        environment_id=scope[1],
        ai_player_session_id=scope[2],
        task_id=scope[3],
        provider=scope[4],
        model=scope[5],
        worker_attempt_count=len(manifests),
        actual_invocation_count=count,
        count_status="measured" if count is not None else "unavailable",
        invocations=invocations,
        observed_usage_sums=_observed_sums(invocations),
        usage_coverage=CoverageV1(
            invocation_count=count,
            measured_counts=measured_counts,
            measured_rates=measured_rates,
            completely_unavailable_invocations=unavailable_count,
            receipt_count=len(receipt_links),
        ),
        latency=LatencySummaryV1(
            wall_measured_count=len(wall_values),
            wall_observed_sum_ms=sum(wall_values) if wall_values else None,
            decision_measured_count=len(decision_values),
            decision_observed_sum_ms=(
                sum(decision_values) if decision_values else None
            ),
        ),
        receipt_links=receipt_links,
    )


__all__ = [
    "ATTEMPT_MANIFEST_SCHEMA",
    "COLLECTION_SCHEMA",
    "PLANNER_BINDING_SCHEMA",
    "HashedFileV1",
    "NullableUsageV1",
    "PlannerBindingV1",
    "ReceiptLinkV1",
    "TrainingPlannerInvocationV1",
    "TrainingPlannerMeasurementCollectionV1",
    "WorkerAttemptManifestRefV1",
    "WorkerAttemptManifestV1",
    "collect_training_planner_measurements",
]
