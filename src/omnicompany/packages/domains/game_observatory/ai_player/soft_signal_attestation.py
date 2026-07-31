"""Trusted Ed25519 attestations for independent AI-player soft-signal reviews."""

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

from .contracts import (
    PlayerSoftSignalReviewAttestationV1,
    PlayerSoftSignalReviewerRole,
    PlayerSoftSignalReviewTrustScope,
    PlayerSoftSignalReviewV1,
)


class PlayerSoftSignalReviewerPublicKeyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1)
    reviewer_role: PlayerSoftSignalReviewerRole
    key_id: str = Field(min_length=1)
    public_key_base64: str = Field(min_length=1)
    status: Literal["active", "revoked"] = "active"
    trust_scope: PlayerSoftSignalReviewTrustScope = "formal_external"


def soft_signal_review_signing_bytes(review: PlayerSoftSignalReviewV1) -> bytes:
    return json.dumps(
        review.attestation_payload(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class PlayerSoftSignalReviewSigner:
    """Private-key holder for a reviewer process that did not execute the game actions."""

    def __init__(
        self,
        *,
        reviewer_id: str,
        reviewer_role: PlayerSoftSignalReviewerRole,
        reviewer_run_id: str,
        key_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        self.reviewer_id = reviewer_id
        self.reviewer_role = reviewer_role
        self.reviewer_run_id = reviewer_run_id
        self.key_id = key_id
        self._private_key = private_key

    @classmethod
    def generate(
        cls,
        *,
        reviewer_id: str,
        reviewer_role: PlayerSoftSignalReviewerRole,
        reviewer_run_id: str,
        key_id: str,
    ) -> "PlayerSoftSignalReviewSigner":
        return cls(
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            reviewer_run_id=reviewer_run_id,
            key_id=key_id,
            private_key=Ed25519PrivateKey.generate(),
        )

    @classmethod
    def from_private_bytes(
        cls,
        *,
        reviewer_id: str,
        reviewer_role: PlayerSoftSignalReviewerRole,
        reviewer_run_id: str,
        key_id: str,
        private_key_bytes: bytes,
    ) -> "PlayerSoftSignalReviewSigner":
        """Load a raw Ed25519 key without exposing it through a public contract."""

        if len(private_key_bytes) != 32:
            raise ValueError("soft-signal reviewer private key must contain 32 raw bytes")
        return cls(
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            reviewer_run_id=reviewer_run_id,
            key_id=key_id,
            private_key=Ed25519PrivateKey.from_private_bytes(private_key_bytes),
        )

    def private_bytes(self) -> bytes:
        """Return raw key material for a local key writer; callers must restrict the file."""

        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_identity(
        self,
        *,
        trust_scope: PlayerSoftSignalReviewTrustScope = "formal_external",
    ) -> PlayerSoftSignalReviewerPublicKeyV1:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return PlayerSoftSignalReviewerPublicKeyV1(
            reviewer_id=self.reviewer_id,
            reviewer_role=self.reviewer_role,
            key_id=self.key_id,
            public_key_base64=base64.b64encode(raw).decode("ascii"),
            trust_scope=trust_scope,
        )

    def sign(self, review: PlayerSoftSignalReviewV1) -> PlayerSoftSignalReviewV1:
        if review.reviewer_id != self.reviewer_id:
            raise ValueError("soft-signal review identity does not match the signer")
        if review.reviewer_role != self.reviewer_role:
            raise ValueError("soft-signal review role does not match the signer")
        if review.attestation is not None:
            raise ValueError("soft-signal review is already attested")
        payload = soft_signal_review_signing_bytes(review)
        attestation = PlayerSoftSignalReviewAttestationV1(
            reviewer_id=self.reviewer_id,
            reviewer_role=self.reviewer_role,
            reviewer_run_id=self.reviewer_run_id,
            key_id=self.key_id,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            signature_base64=base64.b64encode(self._private_key.sign(payload)).decode("ascii"),
        )
        return review.model_copy(update={"attestation": attestation})


class PlayerSoftSignalReviewerTrustStore:
    ENV_NAME = "OMNICOMPANY_AI_PLAYER_SOFT_SIGNAL_REVIEWER_KEYS_JSON"
    FILE_ENV_NAME = "OMNICOMPANY_AI_PLAYER_SOFT_SIGNAL_REVIEWER_KEYS_FILE"
    DEFAULT_RELATIVE_PATH = Path("runtime") / "soft_signal_reviewer" / "trusted-reviewers.json"

    def __init__(
        self,
        identities: Iterable[PlayerSoftSignalReviewerPublicKeyV1] = (),
    ) -> None:
        self._identities: dict[str, PlayerSoftSignalReviewerPublicKeyV1] = {}
        for identity in identities:
            if identity.key_id in self._identities:
                raise ValueError(f"duplicate soft-signal reviewer key id: {identity.key_id}")
            self._identities[identity.key_id] = identity

    @classmethod
    def from_environment(cls) -> "PlayerSoftSignalReviewerTrustStore":
        encoded = os.environ.get(cls.ENV_NAME)
        if encoded:
            return cls.from_json(encoded, source=cls.ENV_NAME)

        configured_path = os.environ.get(cls.FILE_ENV_NAME)
        if configured_path:
            path = Path(configured_path).expanduser().resolve()
        else:
            # Import lazily to keep the trust primitive independent of storage startup.
            from ..store import default_observatory_root

            path = default_observatory_root() / cls.DEFAULT_RELATIVE_PATH
        if not path.is_file():
            return cls()
        return cls.from_json(path.read_text(encoding="utf-8"), source=str(path))

    @classmethod
    def from_json(
        cls,
        encoded: str,
        *,
        source: str = "soft-signal reviewer trust root",
    ) -> "PlayerSoftSignalReviewerTrustStore":
        values = json.loads(encoded)
        if not isinstance(values, list):
            raise ValueError(f"{source} must contain a JSON list")
        return cls(PlayerSoftSignalReviewerPublicKeyV1.model_validate(item) for item in values)

    @classmethod
    def default_file(cls, observatory_root: Path) -> Path:
        return observatory_root.resolve() / cls.DEFAULT_RELATIVE_PATH

    @property
    def identities(self) -> tuple[PlayerSoftSignalReviewerPublicKeyV1, ...]:
        return tuple(self._identities[key] for key in sorted(self._identities))

    @property
    def configured(self) -> bool:
        return bool(self._identities)

    def verify(self, review: PlayerSoftSignalReviewV1) -> None:
        attestation = review.attestation
        if attestation is None:
            raise ValueError("soft-signal review requires a trusted reviewer attestation")
        identity = self._identities.get(attestation.key_id)
        if identity is None or identity.status != "active":
            raise ValueError("soft-signal reviewer key is not an active trust root")
        if identity.reviewer_id != review.reviewer_id:
            raise ValueError("soft-signal reviewer identity is not bound to the trusted key")
        if identity.reviewer_role != review.reviewer_role:
            raise ValueError("soft-signal reviewer role is not bound to the trusted key")
        if identity.trust_scope != review.trust_scope:
            raise ValueError("soft-signal review trust scope is not bound to the trusted key")
        payload = soft_signal_review_signing_bytes(review)
        if attestation.payload_sha256 != hashlib.sha256(payload).hexdigest():
            raise ValueError("soft-signal review attestation payload hash does not match")
        try:
            signature = base64.b64decode(attestation.signature_base64, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(identity.public_key_base64, validate=True)
            )
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError) as error:
            raise ValueError("soft-signal reviewer signature is invalid") from error


__all__ = [
    "PlayerSoftSignalReviewerPublicKeyV1",
    "PlayerSoftSignalReviewSigner",
    "PlayerSoftSignalReviewerTrustStore",
    "soft_signal_review_signing_bytes",
]
