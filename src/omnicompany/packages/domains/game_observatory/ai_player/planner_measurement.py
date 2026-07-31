"""Append-only, invocation-bound planner usage measurements."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field

from .action_quality_producer import ActionDecisionTelemetryV1
from .contracts import EnvironmentScopeV1

if TYPE_CHECKING:
    from .orchestrator import AutonomousExecutionCommandV1
    from .store import AIPlayerStore


TRUSTED_PLANNER_PRODUCER = "omnicompany.ai-player.planner-runtime.v1"
_SYNTHETIC_MARKERS = ("fixture", "synthetic", "test", "memory")


class PlannerMeasurementAttestationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    producer_identity: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_base64: str = Field(min_length=1)


class PlannerRuntimePublicKeyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    producer_identity: Literal["omnicompany.ai-player.planner-runtime.v1"] = (
        TRUSTED_PLANNER_PRODUCER
    )
    key_id: str = Field(min_length=1)
    public_key_base64: str = Field(min_length=1)
    status: Literal["active", "revoked"] = "active"


class PlannerMeasurementReceiptV1(BaseModel):
    """Usage copied by the planner adapter from one completed model invocation."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_id: Literal[
        "game-observatory.ai-player.planner-measurement-receipt.v1"
    ] = Field(
        default="game-observatory.ai-player.planner-measurement-receipt.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    planner_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    producer_identity: Literal["omnicompany.ai-player.planner-runtime.v1"] = (
        TRUSTED_PLANNER_PRODUCER
    )
    invocation_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_input_tokens: int = Field(ge=0)
    model_output_tokens: int = Field(ge=0)
    decision_latency_ms: int = Field(ge=0)
    completed_at: str = Field(min_length=1)
    attestation: PlannerMeasurementAttestationV1 | None = None

    def telemetry(self) -> ActionDecisionTelemetryV1:
        return ActionDecisionTelemetryV1(
            model_input_tokens=self.model_input_tokens,
            model_output_tokens=self.model_output_tokens,
            decision_latency_ms=self.decision_latency_ms,
        )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def planner_measurement_signing_bytes(receipt: PlannerMeasurementReceiptV1) -> bytes:
    return _canonical_json(
        receipt.model_dump(
            mode="json",
            by_alias=True,
            exclude={"attestation"},
        )
    )


class PlannerMeasurementSigner:
    """Private capability held by the actual planner runtime, not command callers."""

    def __init__(
        self,
        *,
        producer_identity: str,
        key_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        self.producer_identity = producer_identity
        self.key_id = key_id
        self._private_key = private_key

    @classmethod
    def generate(cls, *, key_id: str) -> "PlannerMeasurementSigner":
        return cls(
            producer_identity=TRUSTED_PLANNER_PRODUCER,
            key_id=key_id,
            private_key=Ed25519PrivateKey.generate(),
        )

    def public_identity(self) -> PlannerRuntimePublicKeyV1:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return PlannerRuntimePublicKeyV1(
            producer_identity=TRUSTED_PLANNER_PRODUCER,
            key_id=self.key_id,
            public_key_base64=base64.b64encode(raw).decode("ascii"),
        )

    def sign(self, receipt: PlannerMeasurementReceiptV1) -> PlannerMeasurementReceiptV1:
        if receipt.producer_identity != self.producer_identity:
            raise ValueError("planner receipt producer does not match signer")
        if receipt.attestation is not None:
            raise ValueError("planner measurement receipt is already attested")
        payload = planner_measurement_signing_bytes(receipt)
        return receipt.model_copy(
            update={
                "attestation": PlannerMeasurementAttestationV1(
                    producer_identity=self.producer_identity,
                    key_id=self.key_id,
                    payload_sha256=hashlib.sha256(payload).hexdigest(),
                    signature_base64=base64.b64encode(
                        self._private_key.sign(payload)
                    ).decode("ascii"),
                )
            }
        )


class PlannerMeasurementTrustStore:
    """Facility-configured public keys for planner runtime usage receipts."""

    ENV_NAME = "OMNICOMPANY_AI_PLAYER_PLANNER_RUNTIME_KEYS_JSON"
    FILE_ENV_NAME = "OMNICOMPANY_AI_PLAYER_PLANNER_RUNTIME_KEYS_FILE"
    DEFAULT_RELATIVE_PATH = Path("runtime") / "planner" / "trusted-runtimes.json"

    def __init__(self, identities: Iterable[PlannerRuntimePublicKeyV1] = ()) -> None:
        self._identities: dict[str, PlannerRuntimePublicKeyV1] = {}
        for identity in identities:
            if identity.key_id in self._identities:
                raise ValueError(f"duplicate planner runtime key id: {identity.key_id}")
            self._identities[identity.key_id] = identity

    @classmethod
    def from_environment(cls) -> "PlannerMeasurementTrustStore":
        encoded = os.environ.get(cls.ENV_NAME)
        if encoded:
            values = json.loads(encoded)
            if not isinstance(values, list):
                raise ValueError(f"{cls.ENV_NAME} must contain a JSON list")
            return cls(PlannerRuntimePublicKeyV1.model_validate(item) for item in values)
        configured = os.environ.get(cls.FILE_ENV_NAME)
        if configured:
            path = Path(configured).expanduser().resolve()
        else:
            from ..store import default_observatory_root

            path = default_observatory_root() / cls.DEFAULT_RELATIVE_PATH
        if not path.is_file():
            return cls()
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            raise ValueError(f"{path} must contain a JSON list")
        return cls(PlannerRuntimePublicKeyV1.model_validate(item) for item in values)

    @property
    def configured(self) -> bool:
        return bool(self._identities)

    def verify(self, receipt: PlannerMeasurementReceiptV1) -> None:
        attestation = receipt.attestation
        if attestation is None:
            raise ValueError("planner measurement requires runtime attestation")
        identity = self._identities.get(attestation.key_id)
        if identity is None or identity.status != "active":
            raise ValueError("planner runtime key is not an active trust root")
        if (
            identity.producer_identity != receipt.producer_identity
            or attestation.producer_identity != receipt.producer_identity
        ):
            raise ValueError("planner runtime identity is not bound to the trusted key")
        payload = planner_measurement_signing_bytes(receipt)
        if attestation.payload_sha256 != hashlib.sha256(payload).hexdigest():
            raise ValueError("planner measurement attestation payload hash does not match")
        try:
            signature = base64.b64decode(attestation.signature_base64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(identity.public_key_base64, validate=True)
            )
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError) as error:
            raise ValueError("planner measurement runtime signature is invalid") from error


def planner_request_sha256(command: "AutonomousExecutionCommandV1") -> str:
    """Hash the actual planner decision while excluding its later measurement envelope."""

    payload = command.model_dump(
        mode="json",
        by_alias=True,
        exclude={"decision_telemetry", "planner_measurement_artifact_id"},
    )
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def is_pure_synthetic_environment(environment: EnvironmentScopeV1) -> bool:
    """Classify only by identity leaves; a caller-controlled channel is irrelevant."""

    required = (
        environment.id,
        environment.game_id,
        environment.build_scope_id,
        environment.account_scope_id,
        environment.device_scope_id,
    )
    optional = (
        environment.server_scope_id,
        environment.world_scope_id,
        *environment.game_id_aliases,
        *environment.build_scope_id_aliases,
        *environment.device_scope_id_aliases,
    )
    values = [*required, *(value for value in optional if value is not None)]
    return all(
        any(marker in value.lower() for marker in _SYNTHETIC_MARKERS)
        for value in values
    )


def resolve_planner_telemetry(
    player_store: "AIPlayerStore",
    *,
    environment: EnvironmentScopeV1,
    command: "AutonomousExecutionCommandV1",
    task_id: str | None = None,
) -> tuple[ActionDecisionTelemetryV1, PlannerMeasurementReceiptV1 | None]:
    """Resolve formal usage from the pinned receipt; synthetic scopes may use fixtures."""

    artifact_id = command.planner_measurement_artifact_id
    synthetic = is_pure_synthetic_environment(environment)
    if artifact_id is None:
        if synthetic and command.decision_telemetry is not None:
            return command.decision_telemetry, None
        raise ValueError("planner measurement receipt is required")
    receipt = player_store.get_planner_measurement_receipt_by_artifact(artifact_id)
    if receipt is None:
        raise ValueError("planner measurement receipt is not registered")
    expected = (
        command.environment_id,
        command.session_id,
        command.planned_task_id,
        command.command_id,
        planner_request_sha256(command),
        artifact_id,
    )
    actual = (
        receipt.environment_id,
        receipt.session_id,
        receipt.task_id,
        receipt.command_id,
        receipt.planner_request_sha256,
        receipt.artifact_id,
    )
    if actual != expected:
        raise ValueError("planner measurement receipt does not bind this command")
    if task_id is not None and receipt.task_id != task_id:
        raise ValueError("planner measurement receipt binds another task")
    telemetry = receipt.telemetry()
    if command.decision_telemetry is not None and command.decision_telemetry != telemetry:
        raise ValueError("caller telemetry contradicts planner measurement receipt")
    return telemetry, receipt


__all__ = [
    "PlannerMeasurementAttestationV1",
    "PlannerMeasurementReceiptV1",
    "PlannerMeasurementSigner",
    "PlannerMeasurementTrustStore",
    "PlannerRuntimePublicKeyV1",
    "TRUSTED_PLANNER_PRODUCER",
    "is_pure_synthetic_environment",
    "planner_request_sha256",
    "planner_measurement_signing_bytes",
    "resolve_planner_telemetry",
]
