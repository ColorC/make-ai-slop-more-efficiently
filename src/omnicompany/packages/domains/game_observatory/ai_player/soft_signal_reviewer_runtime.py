"""Local independent-reviewer runtime for evidence-bound AI-player soft signals.

The runtime deliberately separates three kinds of data:

* external reviewers sign the exact formal payload with a pre-registered public key;
* local keys remain development-only and their reviews never count toward formal gates;
* raw local Ed25519 private key bytes remain in a restricted ignored directory.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import ArtifactRef, EvidenceRun, EvidenceStep, NormalizedAction, utc_now
from ..store import ObservatoryStore
from ..subprocess_policy import headless_process_kwargs
from .contracts import (
    ActionQualitySampleV1,
    EvidenceReferenceV1,
    PlayerSoftSignalReviewerRole,
    PlayerSoftSignalReviewTrustScope,
    PlayerSoftSignalV1,
    PlayerSoftSignalReviewV1,
)
from .soft_signal_attestation import (
    PlayerSoftSignalReviewerPublicKeyV1,
    PlayerSoftSignalReviewerTrustStore,
    PlayerSoftSignalReviewSigner,
)
from .store import AIPlayerStore


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_SUBMISSION_BYTES = 1024 * 1024
_PRIVATE_FILE_SUFFIX = ".private-key.json"


class LocalSoftSignalReviewerKeyV1(BaseModel):
    """Private local configuration. This contract must never enter a canonical store."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: Literal[
        "game-observatory.ai-player.local-soft-signal-reviewer-key.v1"
    ] = Field(
        default="game-observatory.ai-player.local-soft-signal-reviewer-key.v1",
        alias="schema",
    )
    reviewer_id: str = Field(min_length=1)
    reviewer_role: PlayerSoftSignalReviewerRole
    key_id: str = Field(min_length=1)
    private_key_base64: str = Field(min_length=1)
    trust_scope: Literal["development_only"] = "development_only"
    created_at: str = Field(default_factory=utc_now)

    @field_validator("reviewer_id", "key_id")
    @classmethod
    def validate_safe_identifier(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("local reviewer and key ids must be filesystem-safe identifiers")
        return value

    def private_key_bytes(self) -> bytes:
        try:
            raw = base64.b64decode(self.private_key_base64, validate=True)
        except ValueError as error:
            raise ValueError("local reviewer private key is not valid Base64") from error
        if len(raw) != 32:
            raise ValueError("local reviewer private key must contain 32 raw Ed25519 bytes")
        return raw


class SoftSignalReviewSubmissionV1(BaseModel):
    """Judgement authored by an external reviewer agent; identity is injected by the signer."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: Literal[
        "game-observatory.ai-player.soft-signal-review-submission.v1"
    ] = Field(
        default="game-observatory.ai-player.soft-signal-review-submission.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    sample_ids: list[str] = Field(min_length=1)
    reviewer_session_id: str = Field(min_length=1)
    signals: list[PlayerSoftSignalV1] = Field(min_length=1)
    responds_to_request_id: str | None = Field(default=None, min_length=1)
    reviewed_at: str = Field(min_length=1)

    @field_validator("sample_ids")
    @classmethod
    def unique_samples(cls, value: list[str]) -> list[str]:
        if any(not sample_id.strip() for sample_id in value):
            raise ValueError("soft-signal review sample ids must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("soft-signal review sample ids must be unique")
        return value


class SoftSignalReviewBundleV1(BaseModel):
    """Read-only canonical context handed to an external reviewer agent."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.soft-signal-review-bundle.v1"] = Field(
        default="game-observatory.ai-player.soft-signal-review-bundle.v1",
        alias="schema",
    )
    environment_id: str
    sample_ids: list[str]
    samples: list[dict[str, Any]]
    evidence_runs: list[dict[str, Any]]
    evidence_steps: list[dict[str, Any]]
    signal_names: list[str]
    generated_at: str = Field(default_factory=utc_now)


def reviewer_runtime_root(observatory_root: Path) -> Path:
    return (
        observatory_root.resolve()
        / "runtime"
        / "soft_signal_reviewer"
    )


def private_key_path(observatory_root: Path, key_id: str) -> Path:
    if not _SAFE_ID.fullmatch(key_id):
        raise ValueError("reviewer key id must be a filesystem-safe identifier")
    return reviewer_runtime_root(observatory_root) / "private_keys" / f"{key_id}{_PRIVATE_FILE_SUFFIX}"


def trust_root_path(observatory_root: Path) -> Path:
    return PlayerSoftSignalReviewerTrustStore.default_file(observatory_root)


def development_trust_root_path(observatory_root: Path) -> Path:
    return reviewer_runtime_root(observatory_root) / "development-reviewers.json"


def _windows_current_sid() -> str:
    result = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **headless_process_kwargs(),
    )
    rows = list(csv.reader(result.stdout.splitlines()))
    if len(rows) != 1 or len(rows[0]) < 2 or not rows[0][1].startswith("S-"):
        raise RuntimeError("could not resolve the current Windows user SID")
    return rows[0][1]


def _apply_windows_private_acl(path: Path, *, directory: bool) -> None:
    sid = _windows_current_sid()
    permission = "(OI)(CI)F" if directory else "F"
    subprocess.run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:({permission})" if permission == "F" else f"*{sid}:{permission}",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **headless_process_kwargs(),
    )


def restrict_private_path(path: Path, *, directory: bool) -> None:
    """Restrict a key file/directory to the current OS user, failing closed."""

    os.chmod(path, 0o700 if directory else 0o600)
    if os.name == "nt":
        _apply_windows_private_acl(path, directory=directory)


def assert_private_path_restricted(path: Path, *, directory: bool) -> None:
    if not path.exists():
        raise ValueError(f"local reviewer private path does not exist: {path}")
    if os.name == "nt":
        result = subprocess.run(
            ["icacls.exe", str(path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **headless_process_kwargs(),
        )
        acl = result.stdout.lower()
        broad_identities = (
            "everyone",
            "authenticated users",
            "builtin\\users",
            "all application packages",
            "所有人",
            "经过身份验证的用户",
        )
        if "(i)" in acl or any(identity in acl for identity in broad_identities):
            raise ValueError(f"local reviewer private path ACL is not restricted: {path}")
        return
    mode = path.stat().st_mode & 0o777
    forbidden = 0o077
    if mode & forbidden:
        raise ValueError(f"local reviewer private path permissions are too broad: {path}")


def _atomic_write(path: Path, content: bytes, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        restrict_private_path(path.parent, directory=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if private:
            restrict_private_path(temporary, directory=False)
        os.replace(temporary, path)
        if private:
            restrict_private_path(path, directory=False)
            assert_private_path_restricted(path.parent, directory=True)
            assert_private_path_restricted(path, directory=False)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_json_bytes(value: BaseModel | list[dict[str, Any]] | dict[str, Any]) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _load_trust_identities(path: Path) -> list[PlayerSoftSignalReviewerPublicKeyV1]:
    if not path.is_file():
        return []
    return list(
        PlayerSoftSignalReviewerTrustStore.from_json(
            path.read_text(encoding="utf-8"), source=str(path)
        ).identities
    )


def _install_public_identity(
    path: Path,
    identity: PlayerSoftSignalReviewerPublicKeyV1,
) -> Path:
    identities = _load_trust_identities(path)
    by_id = {item.key_id: item for item in identities}
    existing = by_id.get(identity.key_id)
    if existing is not None and existing != identity:
        raise ValueError(f"reviewer trust root already contains a different key: {identity.key_id}")
    by_id[identity.key_id] = identity
    payload = [
        item.model_dump(mode="json")
        for item in sorted(by_id.values(), key=lambda candidate: candidate.key_id)
    ]
    _atomic_write(path, _canonical_json_bytes(payload) + b"\n", private=False)
    return path


def _assert_not_local_development_identity(
    observatory_root: Path,
    identity: PlayerSoftSignalReviewerPublicKeyV1,
) -> None:
    development_identities = _load_trust_identities(
        development_trust_root_path(observatory_root)
    )
    if private_key_path(observatory_root, identity.key_id).exists() or any(
        candidate.key_id == identity.key_id
        or candidate.public_key_base64 == identity.public_key_base64
        for candidate in development_identities
    ):
        raise ValueError(
            "a local development reviewer key cannot be promoted to a formal trust root"
        )


def register_formal_reviewer_identity(
    observatory_root: Path,
    identity: PlayerSoftSignalReviewerPublicKeyV1,
) -> Path:
    """Explicitly configure one external public key as a formal trust root."""

    if identity.trust_scope != "formal_external":
        raise ValueError("formal reviewer registration requires a formal_external public key")
    _assert_not_local_development_identity(observatory_root, identity)
    return _install_public_identity(trust_root_path(observatory_root), identity)


def initialize_local_reviewer_key(
    observatory_root: Path,
    *,
    reviewer_id: str,
    reviewer_role: PlayerSoftSignalReviewerRole,
    key_id: str,
) -> tuple[Path, Path, PlayerSoftSignalReviewerPublicKeyV1]:
    path = private_key_path(observatory_root, key_id)
    if path.exists():
        raise ValueError(f"local reviewer private key already exists: {path}")
    signer = PlayerSoftSignalReviewSigner.generate(
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        reviewer_run_id="local-key-initialization",
        key_id=key_id,
    )
    private = LocalSoftSignalReviewerKeyV1(
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        key_id=key_id,
        private_key_base64=base64.b64encode(signer.private_bytes()).decode("ascii"),
    )
    try:
        _atomic_write(path, _canonical_json_bytes(private) + b"\n", private=True)
        identity = signer.public_identity(trust_scope="development_only")
        trust_path = _install_public_identity(
            development_trust_root_path(observatory_root), identity
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path, trust_path, identity


def _decode_imported_private_key(content: bytes) -> bytes:
    if content.startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(content, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("imported PEM is not an Ed25519 private key")
        return key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    if len(content) == 32:
        return content
    try:
        decoded = base64.b64decode(content.strip(), validate=True)
    except ValueError as error:
        raise ValueError("imported reviewer key must be raw, Base64, PEM, or local-key JSON") from error
    if len(decoded) != 32:
        raise ValueError("imported Ed25519 private key must contain 32 raw bytes")
    return decoded


def import_local_reviewer_key(
    observatory_root: Path,
    *,
    source: Path,
    reviewer_id: str,
    reviewer_role: PlayerSoftSignalReviewerRole,
    key_id: str,
) -> tuple[Path, Path, PlayerSoftSignalReviewerPublicKeyV1]:
    destination = private_key_path(observatory_root, key_id)
    if destination.exists():
        raise ValueError(f"local reviewer private key already exists: {destination}")
    content = source.read_bytes()
    imported: LocalSoftSignalReviewerKeyV1 | None = None
    try:
        imported = LocalSoftSignalReviewerKeyV1.model_validate_json(content)
    except (ValueError, UnicodeDecodeError):
        pass
    if imported is not None:
        if (
            imported.reviewer_id != reviewer_id
            or imported.reviewer_role != reviewer_role
            or imported.key_id != key_id
        ):
            raise ValueError("imported local-key identity does not match the requested reviewer")
        raw = imported.private_key_bytes()
    else:
        raw = _decode_imported_private_key(content)
    signer = PlayerSoftSignalReviewSigner.from_private_bytes(
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        reviewer_run_id="local-key-import",
        key_id=key_id,
        private_key_bytes=raw,
    )
    private = LocalSoftSignalReviewerKeyV1(
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        key_id=key_id,
        private_key_base64=base64.b64encode(raw).decode("ascii"),
    )
    try:
        _atomic_write(destination, _canonical_json_bytes(private) + b"\n", private=True)
        identity = signer.public_identity(trust_scope="development_only")
        trust_path = _install_public_identity(
            development_trust_root_path(observatory_root), identity
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination, trust_path, identity


def load_local_reviewer_signer(
    observatory_root: Path,
    *,
    key_id: str,
    reviewer_run_id: str,
) -> PlayerSoftSignalReviewSigner:
    path = private_key_path(observatory_root, key_id)
    assert_private_path_restricted(path.parent, directory=True)
    assert_private_path_restricted(path, directory=False)
    private = LocalSoftSignalReviewerKeyV1.model_validate_json(path.read_text(encoding="utf-8"))
    if private.key_id != key_id:
        raise ValueError("local reviewer private-key file is bound to another key id")
    signer = PlayerSoftSignalReviewSigner.from_private_bytes(
        reviewer_id=private.reviewer_id,
        reviewer_role=private.reviewer_role,
        reviewer_run_id=reviewer_run_id,
        key_id=private.key_id,
        private_key_bytes=private.private_key_bytes(),
    )
    trust_path = development_trust_root_path(observatory_root)
    if not trust_path.is_file():
        raise ValueError("development soft-signal reviewer registry is not configured")
    trust = PlayerSoftSignalReviewerTrustStore.from_json(
        trust_path.read_text(encoding="utf-8"), source=str(trust_path)
    )
    identity = {item.key_id: item for item in trust.identities}.get(key_id)
    if identity is None or identity.status != "active":
        raise ValueError("development soft-signal reviewer key is not active")
    if identity != signer.public_identity(trust_scope="development_only"):
        raise ValueError("local reviewer private key does not match its development registry")
    return signer


def _canonical_samples(
    player: AIPlayerStore,
    environment_id: str,
    sample_ids: Sequence[str],
) -> list[ActionQualitySampleV1]:
    if player.get_environment(environment_id) is None:
        raise ValueError(f"unknown AI-player environment: {environment_id}")
    samples = [player.get_action_quality_sample(environment_id, sample_id) for sample_id in sample_ids]
    if any(sample is None for sample in samples):
        missing = [sample_id for sample_id, sample in zip(sample_ids, samples) if sample is None]
        raise ValueError(f"soft-signal review samples do not exist: {', '.join(missing)}")
    return [sample for sample in samples if sample is not None]


def build_review_bundle(
    player: AIPlayerStore,
    *,
    environment_id: str,
    sample_ids: Sequence[str],
) -> SoftSignalReviewBundleV1:
    samples = _canonical_samples(player, environment_id, sample_ids)
    run_ids = sorted(
        {
            run_id
            for sample in samples
            for reference in sample.evidence_refs
            for run_id in reference.evidence_run_ids
        }
    )
    step_ids = sorted(
        {
            step_id
            for sample in samples
            for reference in sample.evidence_refs
            for step_id in reference.evidence_step_ids
        }
    )
    runs = [player.observatory_store.get_evidence_run(run_id) for run_id in run_ids]
    steps = [player.observatory_store.get_evidence_step(step_id) for step_id in step_ids]
    if any(run is None for run in runs) or any(step is None for step in steps):
        raise ValueError("soft-signal review bundle contains dead evidence references")
    return SoftSignalReviewBundleV1(
        environment_id=environment_id,
        sample_ids=list(sample_ids),
        samples=[sample.model_dump(mode="json", by_alias=True) for sample in samples],
        evidence_runs=[run.model_dump(mode="json") for run in runs if run is not None],
        evidence_steps=[step.model_dump(mode="json") for step in steps if step is not None],
        signal_names=[
            "tutorial_comprehension",
            "intent_coherence",
            "opportunity_awareness",
            "strategic_continuity",
            "curiosity_quality",
            "loop_avoidance",
            "player_naturalness",
        ],
    )


def load_submission(path: Path) -> SoftSignalReviewSubmissionV1:
    if path.stat().st_size > _MAX_SUBMISSION_BYTES:
        raise ValueError("soft-signal review submission exceeds 1 MiB")
    return SoftSignalReviewSubmissionV1.model_validate_json(path.read_text(encoding="utf-8"))


def _stable_review_id(submission: SoftSignalReviewSubmissionV1, key_id: str) -> str:
    digest = hashlib.sha256(
        _canonical_json_bytes(
            {
                "submission": submission.model_dump(mode="json", by_alias=True),
                "key_id": key_id,
            }
        )
    ).hexdigest()
    return f"soft-review.{digest[:32]}"


def _save_submission_artifact(
    observatory: ObservatoryStore,
    *,
    environment_id: str,
    review_id: str,
    review_run_id: str,
    submission: SoftSignalReviewSubmissionV1,
) -> ArtifactRef:
    raw = _canonical_json_bytes(submission) + b"\n"
    digest = hashlib.sha256(raw).hexdigest()
    directory = observatory.artifact_root / "soft_signal_reviews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{review_id}.submission.json"
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("soft-signal review submission artifact would overwrite different content")
    if not path.exists():
        _atomic_write(path, raw, private=False)
    artifact = ArtifactRef(
        id=f"art.{review_id}.submission",
        kind="source",
        path=str(path),
        sha256=digest,
        captured_at=submission.reviewed_at,
        run_id=review_run_id,
        media_type="application/json",
        metadata={
            "environment_id": environment_id,
            "review_id": review_id,
            "contains_private_key": False,
            "authored_by_external_reviewer": True,
            "public": False,
        },
    )
    observatory.save_artifact(artifact)
    return artifact


def _build_unsigned_review(
    player: AIPlayerStore,
    submission: SoftSignalReviewSubmissionV1,
    *,
    identity: PlayerSoftSignalReviewerPublicKeyV1,
) -> PlayerSoftSignalReviewV1:
    samples = _canonical_samples(player, submission.environment_id, submission.sample_ids)
    subject_session_ids = sorted({sample.session_id for sample in samples})
    if submission.reviewer_session_id in subject_session_ids:
        raise ValueError("reviewer session cannot be one of the executing sessions")
    subject_step_ids = {
        step_id
        for sample in samples
        for reference in sample.evidence_refs
        for step_id in reference.evidence_step_ids
    }
    cited_step_ids = {
        step_id for signal in submission.signals for step_id in signal.evidence_step_ids
    }
    if not cited_step_ids.issubset(subject_step_ids):
        raise ValueError("soft-signal scores must cite reviewed action evidence steps")
    if identity.reviewer_id in {
        actor
        for sample in samples
        for actor in _sample_executing_actors(player, sample)
    }:
        raise ValueError("soft-signal reviewer also executed the reviewed session")
    review_id = _stable_review_id(submission, identity.key_id)
    review_run_id = f"evidence-run.{review_id}"
    review_step_id = f"evidence-step.{review_id}"
    review_reference = EvidenceReferenceV1(
        environment_id=submission.environment_id,
        artifact_ids=[f"art.{review_id}.submission"],
        evidence_run_ids=[review_run_id],
        evidence_step_ids=[review_step_id],
    )
    return PlayerSoftSignalReviewV1(
        id=review_id,
        environment_id=submission.environment_id,
        evidence_refs=[review_reference],
        sample_ids=list(submission.sample_ids),
        subject_session_ids=subject_session_ids,
        reviewer_id=identity.reviewer_id,
        reviewer_role=identity.reviewer_role,
        trust_scope=identity.trust_scope,
        reviewer_session_id=submission.reviewer_session_id,
        review_evidence_run_id=review_run_id,
        review_evidence_step_id=review_step_id,
        signals=list(submission.signals),
        responds_to_request_id=submission.responds_to_request_id,
        reviewed_at=submission.reviewed_at,
    )


def _persist_signed_review(
    player: AIPlayerStore,
    submission: SoftSignalReviewSubmissionV1,
    signed: PlayerSoftSignalReviewV1,
    *,
    trust_store: PlayerSoftSignalReviewerTrustStore,
) -> PlayerSoftSignalReviewV1:
    trust_store.verify(signed)
    existing = player.get_soft_signal_review(submission.environment_id, signed.id)
    if existing is not None:
        if (
            existing.attestation_payload() != signed.attestation_payload()
            or existing.attestation is None
            or signed.attestation is None
            or existing.attestation.key_id != signed.attestation.key_id
        ):
            raise ValueError("stable soft-signal review id already contains different content")
        return existing

    review_id = signed.id
    review_run_id = signed.review_evidence_run_id
    review_step_id = signed.review_evidence_step_id
    artifact = _save_submission_artifact(
        player.observatory_store,
        environment_id=submission.environment_id,
        review_id=review_id,
        review_run_id=review_run_id,
        submission=submission,
    )
    samples = _canonical_samples(player, submission.environment_id, submission.sample_ids)
    environment = player.get_environment(submission.environment_id)
    if environment is None:  # guarded by _canonical_samples; keeps type narrowing explicit
        raise ValueError(f"unknown AI-player environment: {submission.environment_id}")
    subject_run = _first_subject_run(player, samples)
    payload_sha256 = signed.compute_attestation_payload_sha256()
    player.observatory_store.save_evidence_run(
        EvidenceRun(
            id=review_run_id,
            target_id=environment.device_scope_id,
            adapter="soft_signal_review",
            status="passed",
            game_id=environment.game_id,
            build_scope_id=environment.build_scope_id,
            scope_id=submission.environment_id,
            viewport_width=subject_run.viewport_width,
            viewport_height=subject_run.viewport_height,
            orientation=subject_run.orientation,
            environment={
                "environment_id": submission.environment_id,
                "game_id": environment.game_id,
                "build_scope_id": environment.build_scope_id,
                "account_scope_id": environment.account_scope_id,
                "device_scope_id": environment.device_scope_id,
                "channel": environment.channel,
                "reviewer_session_id": submission.reviewer_session_id,
            },
            started_at=submission.reviewed_at,
            ended_at=submission.reviewed_at,
            step_ids=[review_step_id],
            artifact_ids=[artifact.id],
        )
    )
    player.observatory_store.save_evidence_step(
        EvidenceStep(
            id=review_step_id,
            evidence_run_id=review_run_id,
            step_index=1,
            status="passed",
            started_at=submission.reviewed_at,
            ended_at=submission.reviewed_at,
            action=NormalizedAction(type="wait", seconds=0),
            viewport_width=subject_run.viewport_width,
            viewport_height=subject_run.viewport_height,
            artifact_ids=[artifact.id],
            metadata={
                "soft_signal_review": {
                    "schema": "game-observatory.ai-player.soft-signal-review-evidence.v1",
                    "environment_id": submission.environment_id,
                    "review_id": review_id,
                    "reviewer_id": signed.reviewer_id,
                    "reviewer_role": signed.reviewer_role,
                    "trust_scope": signed.trust_scope,
                    "sample_ids": list(submission.sample_ids),
                    "payload_sha256": payload_sha256,
                },
                "reviewer_session_id": submission.reviewer_session_id,
                "authored_by_external_reviewer": True,
            },
        )
    )
    if signed.trust_scope == "formal_external":
        player.soft_signal_reviewer_trust_store = trust_store
        return player.append_soft_signal_review(signed)
    return player.append_soft_signal_review(
        signed,
        verification_trust_store=trust_store,
    )


def prepare_external_review(
    player: AIPlayerStore,
    submission: SoftSignalReviewSubmissionV1,
    *,
    key_id: str,
) -> PlayerSoftSignalReviewV1:
    """Build the exact formal payload that an external key holder must sign."""

    path = trust_root_path(player.observatory_store.root)
    if not path.is_file():
        raise ValueError("formal soft-signal reviewer trust root is not configured")
    trust = PlayerSoftSignalReviewerTrustStore.from_json(
        path.read_text(encoding="utf-8"), source=str(path)
    )
    identity = {item.key_id: item for item in trust.identities}.get(key_id)
    if (
        identity is None
        or identity.status != "active"
        or identity.trust_scope != "formal_external"
    ):
        raise ValueError("formal soft-signal reviewer key is not pre-registered")
    _assert_not_local_development_identity(player.observatory_store.root, identity)
    return _build_unsigned_review(player, submission, identity=identity)


def submit_external_signed_review(
    player: AIPlayerStore,
    submission: SoftSignalReviewSubmissionV1,
    signed: PlayerSoftSignalReviewV1,
) -> PlayerSoftSignalReviewV1:
    """Accept a formal review only after external signing against a pre-registered key."""

    if signed.attestation is None or signed.trust_scope != "formal_external":
        raise ValueError("formal soft-signal review requires an external signed payload")
    expected = prepare_external_review(
        player,
        submission,
        key_id=signed.attestation.key_id,
    )
    if signed.attestation_payload() != expected.attestation_payload():
        raise ValueError("signed soft-signal review does not match the prepared submission")
    path = trust_root_path(player.observatory_store.root)
    trust = PlayerSoftSignalReviewerTrustStore.from_json(
        path.read_text(encoding="utf-8"), source=str(path)
    )
    return _persist_signed_review(player, submission, signed, trust_store=trust)


def submit_signed_review(
    player: AIPlayerStore,
    submission: SoftSignalReviewSubmissionV1,
    *,
    key_id: str,
) -> PlayerSoftSignalReviewV1:
    """Create a local development-only review; it never counts toward a formal gate."""

    root = player.observatory_store.root
    review_id = _stable_review_id(submission, key_id)
    signer = load_local_reviewer_signer(
        root,
        key_id=key_id,
        reviewer_run_id=f"reviewer-run.{review_id}",
    )
    identity = signer.public_identity(trust_scope="development_only")
    unsigned = _build_unsigned_review(player, submission, identity=identity)
    signed = signer.sign(unsigned)
    path = development_trust_root_path(root)
    trust = PlayerSoftSignalReviewerTrustStore.from_json(
        path.read_text(encoding="utf-8"), source=str(path)
    )
    return _persist_signed_review(player, submission, signed, trust_store=trust)


def _first_subject_run(
    player: AIPlayerStore,
    samples: Sequence[ActionQualitySampleV1],
) -> EvidenceRun:
    for sample in samples:
        for reference in sample.evidence_refs:
            for run_id in reference.evidence_run_ids:
                run = player.observatory_store.get_evidence_run(run_id)
                if run is not None:
                    return run
    raise ValueError("soft-signal review samples do not retain an evidence run")


def _sample_executing_actors(
    player: AIPlayerStore,
    sample: ActionQualitySampleV1,
) -> set[str]:
    actors: set[str] = set()
    with player._connection() as connection:
        rows = connection.execute(
            "SELECT actor FROM ai_player_session_lifecycle_events WHERE session_id=?",
            (sample.session_id,),
        ).fetchall()
    actors.update(str(row["actor"]) for row in rows)
    if sample.evidence_step_id:
        step = player.observatory_store.get_evidence_step(sample.evidence_step_id)
        autonomous = step.metadata.get("autonomous_execution") if step is not None else None
        if isinstance(autonomous, dict):
            for field in ("actor", "agent_id", "executor_actor", "operator"):
                value = autonomous.get(field)
                if isinstance(value, str) and value.strip():
                    actors.add(value)
    return actors


def reviewer_status(observatory_root: Path, *, key_id: str) -> dict[str, Any]:
    key_path = private_key_path(observatory_root, key_id)
    formal_path = trust_root_path(observatory_root)
    development_path = development_trust_root_path(observatory_root)
    formal_identity = next(
        (item for item in _load_trust_identities(formal_path) if item.key_id == key_id),
        None,
    )
    development_identity = next(
        (
            item
            for item in _load_trust_identities(development_path)
            if item.key_id == key_id
        ),
        None,
    )
    private_restricted = False
    if key_path.is_file():
        assert_private_path_restricted(key_path.parent, directory=True)
        assert_private_path_restricted(key_path, directory=False)
        private_restricted = True
    return {
        "schema": "game-observatory.ai-player.soft-signal-reviewer-status.v1",
        "key_id": key_id,
        "private_key_configured": key_path.is_file(),
        "private_key_restricted": private_restricted,
        "private_key_path": str(key_path),
        "trust_scope": "development_only" if development_identity is not None else None,
        "development_registry_configured": (
            development_identity is not None and development_identity.status == "active"
        ),
        "development_registry_path": str(development_path),
        "formal_trust_root_configured": (
            formal_identity is not None and formal_identity.status == "active"
        ),
        "formal_trust_root_path": str(formal_path),
        "reviewer_id": (
            development_identity.reviewer_id
            if development_identity is not None
            else formal_identity.reviewer_id if formal_identity is not None else None
        ),
        "reviewer_role": (
            development_identity.reviewer_role
            if development_identity is not None
            else formal_identity.reviewer_role if formal_identity is not None else None
        ),
    }


__all__ = [
    "LocalSoftSignalReviewerKeyV1",
    "SoftSignalReviewBundleV1",
    "SoftSignalReviewSubmissionV1",
    "assert_private_path_restricted",
    "build_review_bundle",
    "development_trust_root_path",
    "import_local_reviewer_key",
    "initialize_local_reviewer_key",
    "load_local_reviewer_signer",
    "load_submission",
    "private_key_path",
    "prepare_external_review",
    "register_formal_reviewer_identity",
    "reviewer_status",
    "submit_signed_review",
    "submit_external_signed_review",
    "trust_root_path",
]
