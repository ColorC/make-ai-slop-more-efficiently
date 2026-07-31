"""Trusted, tamper-evident attestations for canonical skill runs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field

from .contracts import SkillRunAttestationV1, SkillRunV1


class SkillValidatorPublicKeyV1(BaseModel):
    """One administrator-selected validation identity; private keys stay out of the store."""

    model_config = ConfigDict(extra="forbid")

    validator_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    public_key_base64: str = Field(min_length=1)
    status: Literal["active", "revoked"] = "active"


def skill_run_signing_bytes(run: SkillRunV1) -> bytes:
    payload = run.model_dump(
        mode="json",
        by_alias=True,
        exclude={"validator_attestation"},
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class SkillValidationSigner:
    """Held by an independent validator process, never by the skill creator."""

    def __init__(
        self,
        *,
        validator_id: str,
        key_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        self.validator_id = validator_id
        self.key_id = key_id
        self._private_key = private_key

    @classmethod
    def generate(cls, *, validator_id: str, key_id: str) -> "SkillValidationSigner":
        return cls(
            validator_id=validator_id,
            key_id=key_id,
            private_key=Ed25519PrivateKey.generate(),
        )

    @classmethod
    def from_private_key_base64(
        cls,
        *,
        validator_id: str,
        key_id: str,
        private_key_base64: str,
    ) -> "SkillValidationSigner":
        raw = base64.b64decode(private_key_base64, validate=True)
        return cls(
            validator_id=validator_id,
            key_id=key_id,
            private_key=Ed25519PrivateKey.from_private_bytes(raw),
        )

    def public_identity(self) -> SkillValidatorPublicKeyV1:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return SkillValidatorPublicKeyV1(
            validator_id=self.validator_id,
            key_id=self.key_id,
            public_key_base64=base64.b64encode(raw).decode("ascii"),
        )

    def private_key_base64(self) -> str:
        raw = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return base64.b64encode(raw).decode("ascii")

    def sign(self, run: SkillRunV1) -> SkillRunV1:
        if run.validator_id != self.validator_id:
            raise ValueError("skill run validator does not match the independent signer")
        if run.validator_attestation is not None:
            raise ValueError("skill run is already attested")
        payload = skill_run_signing_bytes(run)
        attestation = SkillRunAttestationV1(
            validator_id=self.validator_id,
            key_id=self.key_id,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            signature_base64=base64.b64encode(self._private_key.sign(payload)).decode("ascii"),
        )
        return run.model_copy(update={"validator_attestation": attestation})


SKILL_RUNTIME_SIGNER_ENV = "OMNICOMPANY_AI_PLAYER_SKILL_RUNTIME_SIGNER_JSON"
LOCAL_SKILL_RUNTIME_SIGNER_SCHEMA = (
    "game-observatory.ai-player.local-skill-runtime-signer.v1"
)
LOCAL_SKILL_RUNTIME_VALIDATOR_ID = "ai-player-skill-validator.local"
LOCAL_SKILL_RUNTIME_KEY_ID = "ai-player-skill-validator-key.local.v1"


def skill_runtime_signer_from_environment() -> SkillValidationSigner | None:
    """Load the facility-held runtime signer without exposing key material to Agents."""

    encoded = os.environ.get(SKILL_RUNTIME_SIGNER_ENV)
    if not encoded:
        return None
    try:
        value = json.loads(encoded)
        if not isinstance(value, dict) or set(value) != {
            "validator_id",
            "key_id",
            "private_key_base64",
        }:
            raise ValueError("unexpected signer fields")
        return SkillValidationSigner.from_private_key_base64(
            validator_id=str(value["validator_id"]),
            key_id=str(value["key_id"]),
            private_key_base64=str(value["private_key_base64"]),
        )
    except Exception as error:
        raise ValueError(
            f"{SKILL_RUNTIME_SIGNER_ENV} is invalid; private key material was not loaded"
        ) from error


def load_or_create_local_skill_runtime_signer(
    observatory_root: str | Path,
) -> SkillValidationSigner:
    """Load or atomically create the stable facility-local validator identity."""

    path = (
        Path(observatory_root).expanduser().resolve()
        / "ai_player_runtime"
        / "skill-runtime-signer.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    def decode() -> SkillValidationSigner:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("local skill runtime signer is unreadable") from error
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "validator_id",
            "key_id",
            "private_key_base64",
        }:
            raise ValueError("local skill runtime signer has unexpected fields")
        if value.get("schema") != LOCAL_SKILL_RUNTIME_SIGNER_SCHEMA:
            raise ValueError("local skill runtime signer schema is unsupported")
        return SkillValidationSigner.from_private_key_base64(
            validator_id=str(value["validator_id"]),
            key_id=str(value["key_id"]),
            private_key_base64=str(value["private_key_base64"]),
        )

    if path.is_file():
        return decode()

    signer = SkillValidationSigner.generate(
        validator_id=LOCAL_SKILL_RUNTIME_VALIDATOR_ID,
        key_id=LOCAL_SKILL_RUNTIME_KEY_ID,
    )
    payload = (
        json.dumps(
            {
                "schema": LOCAL_SKILL_RUNTIME_SIGNER_SCHEMA,
                "validator_id": signer.validator_id,
                "key_id": signer.key_id,
                "private_key_base64": signer.private_key_base64(),
            },
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return decode()
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    return signer


def skill_runtime_signer_and_trust_store(
    observatory_root: str | Path,
) -> tuple[SkillValidationSigner, "SkillValidatorTrustStore"]:
    """Resolve an operator signer or provision the stable local facility signer."""

    configured = skill_runtime_signer_from_environment()
    if configured is not None:
        return configured, SkillValidatorTrustStore.from_environment()
    local = load_or_create_local_skill_runtime_signer(observatory_root)
    return local, SkillValidatorTrustStore([local.public_identity()])


class SkillValidatorTrustStore:
    """Read-only public-key registry supplied by facility configuration."""

    ENV_NAME = "OMNICOMPANY_AI_PLAYER_SKILL_VALIDATOR_KEYS_JSON"

    def __init__(self, identities: Iterable[SkillValidatorPublicKeyV1] = ()) -> None:
        self._identities: dict[str, SkillValidatorPublicKeyV1] = {}
        for identity in identities:
            if identity.key_id in self._identities:
                raise ValueError(f"duplicate skill validator key id: {identity.key_id}")
            self._identities[identity.key_id] = identity

    @classmethod
    def from_environment(cls) -> "SkillValidatorTrustStore":
        encoded = os.environ.get(cls.ENV_NAME)
        if not encoded:
            return cls()
        values = json.loads(encoded)
        if not isinstance(values, list):
            raise ValueError(f"{cls.ENV_NAME} must contain a JSON list")
        return cls(SkillValidatorPublicKeyV1.model_validate(value) for value in values)

    def verify(self, run: SkillRunV1) -> None:
        attestation = run.validator_attestation
        if attestation is None:
            raise ValueError("skill run requires a trusted validator attestation")
        identity = self.assert_active(run.validator_id, attestation.key_id)
        payload = skill_run_signing_bytes(run)
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        if attestation.payload_sha256 != payload_sha256:
            raise ValueError("skill run attestation payload hash does not match")
        try:
            signature = base64.b64decode(attestation.signature_base64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(identity.public_key_base64, validate=True)
            )
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError) as error:
            raise ValueError("skill run validator signature is invalid") from error

    def assert_active(
        self,
        validator_id: str,
        key_id: str,
    ) -> SkillValidatorPublicKeyV1:
        identity = self._identities.get(key_id)
        if identity is None or identity.status != "active":
            raise ValueError("skill run validator key is not an active trust root")
        if identity.validator_id != validator_id:
            raise ValueError("skill run validator is not bound to the trusted key")
        return identity


__all__ = [
    "SKILL_RUNTIME_SIGNER_ENV",
    "LOCAL_SKILL_RUNTIME_SIGNER_SCHEMA",
    "SkillValidationSigner",
    "SkillValidatorPublicKeyV1",
    "SkillValidatorTrustStore",
    "skill_runtime_signer_from_environment",
    "load_or_create_local_skill_runtime_signer",
    "skill_runtime_signer_and_trust_store",
    "skill_run_signing_bytes",
]
