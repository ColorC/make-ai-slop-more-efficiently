"""Independent, file-only validation for the AFK physical-play bootstrap gate.

This validator does not create or approve game truth.  It replays the eligibility
diagnostic over detached repository evidence and produces a deterministic result
which can be bound by a separately signed machine-validator receipt.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BENCHMARK_ID = "afk_interaction_preflight_known_controls_v1"
AFK_BENCHMARK_ID = "afk_hero_growth_v1"
DIAGNOSTIC_SCHEMA = "game-observatory.ai-player.afk-physical-readiness-diagnostic.v1"
VALIDATOR_RECEIPT_SCHEMA = (
    "game-observatory.ai-player.afk-physical-readiness-validator-receipt.v1"
)
VALIDATION_SCOPE = "eligibility_diagnostic_only_no_human_truth_adjudication"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class IndependentValidatorIdentityV1(_StrictModel):
    kind: Literal["independent_machine_validator"] = "independent_machine_validator"
    id: str = Field(min_length=1)


class AFKPhysicalReadinessValidatorReceiptV1(_StrictModel):
    schema_id: Literal[VALIDATOR_RECEIPT_SCHEMA] = Field(
        default=VALIDATOR_RECEIPT_SCHEMA, alias="schema"
    )
    id: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    validation_scope: Literal[VALIDATION_SCOPE] = VALIDATION_SCOPE
    validator: IndependentValidatorIdentityV1
    issued_at: datetime
    expected_build_id: str = Field(min_length=1)
    validator_code_sha256: str = Field(pattern=SHA256_PATTERN)
    input_sha256: dict[str, str]
    diagnostic_sha256: str = Field(pattern=SHA256_PATTERN)
    verdict: Literal["PASS", "FAIL"]
    physical_play_unlocked: bool
    signature_base64: str = ""

    @field_validator("issued_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("validator receipt issued_at must include a timezone")
        return value

    @field_validator("input_sha256")
    @classmethod
    def _input_hashes_are_detached(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("validator receipt must bind evidence inputs")
        for name, digest in value.items():
            if not name or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError("validator receipt input hash is invalid")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _verdict_matches_unlock(self) -> AFKPhysicalReadinessValidatorReceiptV1:
        if self.physical_play_unlocked != (self.verdict == "PASS"):
            raise ValueError("validator receipt verdict contradicts physical-play status")
        return self


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def diagnostic_sha256(diagnostic: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(diagnostic)).hexdigest()


def validator_receipt_signing_bytes(
    receipt: AFKPhysicalReadinessValidatorReceiptV1,
) -> bytes:
    return _canonical_bytes(
        receipt.model_dump(
            mode="json",
            by_alias=True,
            exclude={"signature_base64"},
        )
    )


def validator_code_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class AFKPhysicalReadinessValidatorSigner:
    """Signer held by the validator process, never by candidate-production code."""

    def __init__(
        self,
        *,
        validator_id: str,
        private_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self.validator_id = validator_id
        self._private_key = private_key or Ed25519PrivateKey.generate()

    @property
    def public_key_base64(self) -> str:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def sign(
        self,
        receipt: AFKPhysicalReadinessValidatorReceiptV1,
    ) -> AFKPhysicalReadinessValidatorReceiptV1:
        if receipt.validator.id != self.validator_id:
            raise ValueError("validator receipt identity does not match signer")
        if receipt.signature_base64:
            raise ValueError("validator receipt is already signed")
        signature = self._private_key.sign(validator_receipt_signing_bytes(receipt))
        return receipt.model_copy(
            update={"signature_base64": base64.b64encode(signature).decode("ascii")}
        )


class AFKPhysicalReadinessValidatorTrustStore:
    """Explicit public-key trust roots for eligibility diagnostics."""

    def __init__(self, trusted_public_keys: Mapping[str, str]) -> None:
        self._trusted_public_keys = dict(trusted_public_keys)

    def verify(self, receipt: AFKPhysicalReadinessValidatorReceiptV1) -> None:
        encoded = self._trusted_public_keys.get(receipt.validator.id)
        if encoded is None:
            raise ValueError("physical-readiness validator is not trusted")
        if not receipt.signature_base64:
            raise ValueError("physical-readiness validator receipt is unsigned")
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(encoded, validate=True)
            )
            signature = base64.b64decode(receipt.signature_base64, validate=True)
            public_key.verify(signature, validator_receipt_signing_bytes(receipt))
        except (ValueError, TypeError, InvalidSignature) as error:
            raise ValueError("physical-readiness validator receipt signature is invalid") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_repository_file(root: Path, value: str) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not _inside(root, resolved):
        raise ValueError("AFK readiness evidence must remain inside repository_root")
    if not resolved.is_file():
        raise ValueError(f"AFK readiness evidence does not exist: {value}")
    return resolved


def _load_detached_json(
    *,
    path: Path,
    expected_sha256: str,
    input_name: str,
    input_sha256: dict[str, str],
) -> dict[str, Any]:
    actual = _sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"AFK readiness detached hash mismatch: {input_name}")
    input_sha256[input_name] = actual
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"AFK readiness JSON is unreadable: {input_name}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"AFK readiness JSON must be an object: {input_name}")
    return payload


def _bind_nested_ref(
    *,
    repository_root: Path,
    reference: Mapping[str, Any],
    input_name: str,
    input_sha256: dict[str, str],
) -> Path:
    path_value = reference.get("path")
    expected_hash = reference.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        raise ValueError(f"AFK readiness nested reference is invalid: {input_name}")
    path = _resolve_repository_file(repository_root, path_value)
    actual = _sha256_file(path)
    if actual != expected_hash:
        raise ValueError(f"AFK readiness nested hash mismatch: {input_name}")
    input_sha256[input_name] = actual
    return path


def _add_gap(gaps: list[dict[str, Any]], code: str, detail: str, **data: Any) -> None:
    gaps.append({"code": code, "detail": detail, **data})


def validate_afk_physical_readiness_evidence(
    *,
    repository_root: Path,
    expected_build_id: str,
    detached_inputs: Mapping[str, tuple[Path, str]],
) -> dict[str, Any]:
    """Replay all file-only AFK eligibility checks without touching a device."""

    required = {
        "acceptance_manifest",
        "candidate_manifest",
        "candidate_validation",
        "adjudication_evidence",
        "truth_audit_result",
        "interaction_preflight_result",
    }
    if set(detached_inputs) != required:
        missing = sorted(required - set(detached_inputs))
        extra = sorted(set(detached_inputs) - required)
        raise ValueError(f"AFK readiness evidence set mismatch: missing={missing}, extra={extra}")

    input_sha256: dict[str, str] = {}
    documents: dict[str, dict[str, Any]] = {}
    adjudication_parse_error = False
    for name in sorted(required):
        path, expected_hash = detached_inputs[name]
        if name == "adjudication_evidence":
            actual = _sha256_file(path)
            if actual != expected_hash:
                raise ValueError(f"AFK readiness detached hash mismatch: {name}")
            input_sha256[name] = actual
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                adjudication_parse_error = True
            else:
                if not isinstance(payload, dict):
                    adjudication_parse_error = True
                else:
                    documents[name] = payload
            continue
        documents[name] = _load_detached_json(
            path=path,
            expected_sha256=expected_hash,
            input_name=name,
            input_sha256=input_sha256,
        )

    acceptance = documents["acceptance_manifest"]
    candidate = documents["candidate_manifest"]
    validation = documents["candidate_validation"]
    truth_audit = documents["truth_audit_result"]
    preflight = documents["interaction_preflight_result"]
    candidate_path, candidate_hash = detached_inputs["candidate_manifest"]

    if candidate.get("benchmark_id") != AFK_BENCHMARK_ID:
        raise ValueError("AFK candidate benchmark identity mismatch")
    candidate_id = candidate.get("id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("AFK candidate id is missing")
    if validation.get("manifest_id") != candidate_id:
        raise ValueError("AFK candidate validation binds another manifest")

    targets = acceptance.get("targets")
    if not isinstance(targets, list):
        raise ValueError("AFK acceptance manifest targets are missing")
    afk_targets = [item for item in targets if item.get("benchmark_id") == AFK_BENCHMARK_ID]
    if len(afk_targets) != 1:
        raise ValueError("AFK acceptance manifest must contain one AFK target")
    afk_target = afk_targets[0]
    if afk_target.get("candidate_manifest_sha256") != candidate_hash:
        raise ValueError("AFK acceptance manifest candidate hash mismatch")
    if afk_target.get("build_id") != expected_build_id:
        raise ValueError("AFK acceptance manifest build does not match configured build")

    source = candidate.get("source") or {}
    adjudication_ref = source.get("adjudication_fixture") or {}
    if adjudication_ref.get("sha256") != detached_inputs["adjudication_evidence"][1]:
        raise ValueError("AFK candidate adjudication hash mismatch")

    truth_inputs = truth_audit.get("inputs")
    if not isinstance(truth_inputs, dict):
        raise ValueError("AFK truth audit input bindings are missing")
    direct_truth_bindings = {
        "candidate_manifest": "candidate_manifest",
        "candidate_validation": "candidate_validation",
        "afk_evidence_adjudication": "adjudication_evidence",
    }
    for audit_name, direct_name in direct_truth_bindings.items():
        reference = truth_inputs.get(audit_name)
        if not isinstance(reference, dict):
            raise ValueError(f"AFK truth audit reference is missing: {audit_name}")
        if reference.get("sha256") != detached_inputs[direct_name][1]:
            raise ValueError(f"AFK truth audit reference mismatch: {audit_name}")
    for name, reference in sorted(truth_inputs.items()):
        if not isinstance(reference, dict):
            raise ValueError(f"AFK truth audit reference is invalid: {name}")
        _bind_nested_ref(
            repository_root=repository_root,
            reference=reference,
            input_name=f"truth_audit.{name}",
            input_sha256=input_sha256,
        )

    preflight_source = preflight.get("source") or {}
    if preflight_source.get("candidate_manifest_sha256") != candidate_hash:
        raise ValueError("AFK interaction preflight result binds another candidate")
    if preflight.get("benchmark_id") != BENCHMARK_ID:
        raise ValueError("AFK interaction preflight benchmark identity mismatch")

    collections = candidate.get("collections")
    if not isinstance(collections, dict):
        raise ValueError("AFK candidate collections are missing")
    candidate_root = candidate_path.parent
    routes: list[dict[str, Any]] = []
    interruptions: list[dict[str, Any]] = []
    for collection_name, output in (("routes", routes), ("interruptions", interruptions)):
        references = collections.get(collection_name)
        if not isinstance(references, list):
            raise ValueError(f"AFK candidate {collection_name} are missing")
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError(f"AFK candidate {collection_name} reference is invalid")
            relative_path = reference.get("path")
            expected_hash = reference.get("sha256")
            item_id = reference.get("id")
            if not all(isinstance(value, str) and value for value in (relative_path, expected_hash, item_id)):
                raise ValueError(f"AFK candidate {collection_name} reference is incomplete")
            path = (candidate_root / relative_path).resolve()
            if not _inside(repository_root, path):
                raise ValueError(f"AFK candidate {collection_name} path escapes repository")
            payload = _load_detached_json(
                path=path,
                expected_sha256=expected_hash,
                input_name=f"candidate.{collection_name}.{item_id}",
                input_sha256=input_sha256,
            )
            if payload.get("id") != item_id:
                raise ValueError(f"AFK candidate {collection_name} id mismatch: {item_id}")
            output.append(payload)

    gaps: list[dict[str, Any]] = []
    if acceptance.get("freeze_status") not in {"frozen", "approved"} or acceptance.get("manifest_pass") is not True:
        _add_gap(
            gaps,
            "acceptance_manifest_not_frozen",
            "AFK 验收清单仍处于候选审阅阶段，最终验收尚未通过。",
            freeze_status=acceptance.get("freeze_status"),
        )
    if candidate.get("semantic_status") != "human_frozen":
        _add_gap(
            gaps,
            "afk_truth_not_human_frozen",
            "AFK 状态、交互与路线清单仍是候选资料，尚未成为经人类冻结的真值。",
            semantic_status=candidate.get("semantic_status"),
        )
    if candidate.get("frozen") is not True or candidate.get("freeze_pass") is not True:
        _add_gap(
            gaps,
            "afk_truth_freeze_not_passed",
            "AFK 候选清单未通过冻结门。",
        )
    signature_count = int((candidate.get("counts") or {}).get("human_truth_signatures", 0))
    if signature_count < 1 or candidate.get("human_truth_signature") is None:
        _add_gap(
            gaps,
            "afk_human_truth_signature_missing",
            "AFK 候选清单没有可信人类审阅者的最终真值签名。",
            human_truth_signatures=signature_count,
        )
    if validation.get("candidate_structure_pass") is not True:
        _add_gap(
            gaps,
            "afk_candidate_structure_invalid",
            "AFK 候选结构校验未通过。",
        )
    if validation.get("freeze_pass") is not True:
        _add_gap(
            gaps,
            "afk_candidate_validation_not_frozen",
            "AFK 候选结构可读取，但冻结校验仍未通过。",
            freeze_blockers=list(validation.get("freeze_blockers") or []),
        )
    if adjudication_parse_error:
        _add_gap(
            gaps,
            "afk_adjudication_evidence_unparseable",
            "AFK 实机裁决证据文件已做哈希绑定，但当前不是可解析的 JSON。",
        )
    elif (documents.get("adjudication_evidence") or {}).get("human_truth_status") != "signed":
        _add_gap(
            gaps,
            "afk_adjudication_not_human_signed",
            "AFK 实机裁决证据尚未由人类签发。",
        )

    if truth_audit.get("status") != "PASS":
        _add_gap(
            gaps,
            "afk_truth_audit_failed",
            "AFK 真值审计当前未通过。",
            audit_status=truth_audit.get("status"),
        )
    separate_exit = truth_audit.get("separate_exit_conditions") or {}
    if separate_exit.get("truth_unsigned"):
        _add_gap(
            gaps,
            "afk_holdout_truth_unsigned",
            "AFK 真实图像 holdout 尚未完成独立人类签发。",
            remaining_conditions=len(separate_exit["truth_unsigned"]),
        )
    if separate_exit.get("recognition_ambiguity"):
        _add_gap(
            gaps,
            "afk_recognition_ambiguity_unresolved",
            "AFK 真实图像识别仍有未裁决或低分辨率样本。",
            remaining_conditions=len(separate_exit["recognition_ambiguity"]),
        )

    if preflight.get("truth_eligible") is not True:
        _add_gap(
            gaps,
            "interaction_preflight_truth_ineligible",
            "交互预检结果没有建立在人类冻结真值之上。",
        )
    if preflight.get("verdict") != "PASS" or preflight.get("physical_play_unlocked") is not True:
        _add_gap(
            gaps,
            "interaction_preflight_failed",
            "AFK 已知控件交互预检当前未通过。",
        )
    upstream_gap_codes = {
        item.get("code")
        for item in preflight.get("gaps") or []
        if isinstance(item, dict)
    }
    for code, detail in (
        ("preflight_fixture_missing", "缺少与冻结真值绑定的独立交互预检用例集。"),
        ("preflight_labels_missing", "缺少人类冻结的交互预检标签。"),
        ("preflight_predictions_missing", "缺少对冻结输入重新生成的交互预检预测。"),
    ):
        if code in upstream_gap_codes:
            _add_gap(gaps, code, detail)
    if "preflight_labels_missing" in upstream_gap_codes:
        _add_gap(
            gaps,
            "preflight_human_attestation_missing",
            "交互预检尚无可信人类审阅者对冻结标签的独立签名。",
        )

    route_ids = [str(item.get("id")) for item in routes]
    if len(routes) != 8:
        _add_gap(
            gaps,
            "afk_route_fixture_count_incomplete",
            "AFK 已知路线数量未达到固定的 8 条。",
            actual_count=len(routes),
            required_count=8,
        )
    unpassed_routes = [
        str(item.get("id")) for item in routes if item.get("replay_status") != "passed"
    ]
    if unpassed_routes:
        _add_gap(
            gaps,
            "afk_routes_not_replay_verified",
            "AFK 路线尚未全部完成成功重放。",
            route_ids=unpassed_routes,
        )
    route_build_missing = [
        str(item.get("id"))
        for item in routes
        if item.get("build_id", item.get("build_scope_id")) != expected_build_id
    ]
    if route_build_missing:
        _add_gap(
            gaps,
            "afk_current_build_route_evidence_missing",
            "AFK 路线没有全部绑定并验证在配置指定的当前构建上。",
            expected_build_id=expected_build_id,
            route_ids=route_build_missing,
        )
    invalid_recovery_routes = [
        str(item.get("id"))
        for item in routes
        if (item.get("recovery_chain") or {}).get("replay_status")
        == "not_verified_current_build"
    ]
    if invalid_recovery_routes:
        _add_gap(
            gaps,
            "afk_current_build_recovery_not_verified",
            "AFK 失败路线的恢复链尚未在当前构建验证。",
            route_ids=invalid_recovery_routes,
        )
    if candidate.get("frozen") is not True and route_ids:
        _add_gap(
            gaps,
            "afk_routes_not_human_frozen",
            "8 条 AFK 路线仍属于候选清单，尚未完成人类冻结。",
            route_ids=route_ids,
        )

    interruption_ids = [str(item.get("id")) for item in interruptions]
    if len(interruptions) != 6:
        _add_gap(
            gaps,
            "afk_controlled_interruption_count_incomplete",
            "AFK 受控中断场景数量未达到固定的 6 个。",
            actual_count=len(interruptions),
            required_count=6,
        )
    unexecuted_interruptions = [
        str(item.get("id"))
        for item in interruptions
        if item.get("controlled_injection_executed") is not True
    ]
    if unexecuted_interruptions:
        _add_gap(
            gaps,
            "afk_controlled_interruptions_not_executed",
            "AFK 受控中断目前只有定义，尚未实际注入和恢复验证。",
            interruption_ids=unexecuted_interruptions,
        )
    interruption_build_missing = [
        str(item.get("id"))
        for item in interruptions
        if item.get("build_id", item.get("build_scope_id")) != expected_build_id
    ]
    if interruption_build_missing:
        _add_gap(
            gaps,
            "afk_current_build_interruption_evidence_missing",
            "AFK 受控中断没有全部绑定并验证在配置指定的当前构建上。",
            expected_build_id=expected_build_id,
            interruption_ids=interruption_build_missing,
        )
    unfrozen_interruptions = [
        str(item.get("id")) for item in interruptions if item.get("frozen") is not True
    ]
    if unfrozen_interruptions:
        _add_gap(
            gaps,
            "afk_controlled_interruptions_not_frozen",
            "AFK 受控中断结果尚未冻结。",
            interruption_ids=unfrozen_interruptions,
        )

    passed = not gaps
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "benchmark_id": BENCHMARK_ID,
        "expected_build_id": expected_build_id,
        "verdict": "PASS" if passed else "FAIL",
        "physical_play_unlocked": passed,
        "input_sha256": dict(sorted(input_sha256.items())),
        "observations": {
            "candidate_id": candidate_id,
            "candidate_semantic_status": candidate.get("semantic_status"),
            "candidate_frozen": candidate.get("frozen"),
            "human_truth_signatures": signature_count,
            "route_count": len(routes),
            "route_replay_statuses": {
                str(item.get("id")): item.get("replay_status") for item in routes
            },
            "controlled_interruption_count": len(interruptions),
            "controlled_interruptions_executed": len(interruptions)
            - len(unexecuted_interruptions),
            "truth_audit_status": truth_audit.get("status"),
            "interaction_preflight_verdict": preflight.get("verdict"),
        },
        "gaps": gaps,
    }


def verify_validator_receipt(
    *,
    receipt: AFKPhysicalReadinessValidatorReceiptV1,
    diagnostic: Mapping[str, Any],
    trusted_public_keys: Mapping[str, str],
) -> None:
    AFKPhysicalReadinessValidatorTrustStore(trusted_public_keys).verify(receipt)
    if receipt.validator_code_sha256 != validator_code_sha256():
        raise ValueError("physical-readiness validator code hash changed")
    if receipt.expected_build_id != diagnostic.get("expected_build_id"):
        raise ValueError("physical-readiness validator receipt binds another build")
    if receipt.input_sha256 != diagnostic.get("input_sha256"):
        raise ValueError("physical-readiness validator receipt input hashes changed")
    if receipt.diagnostic_sha256 != diagnostic_sha256(diagnostic):
        raise ValueError("physical-readiness validator receipt diagnostic hash changed")
    if receipt.verdict != diagnostic.get("verdict"):
        raise ValueError("physical-readiness validator receipt verdict changed")
    if receipt.physical_play_unlocked != diagnostic.get("physical_play_unlocked"):
        raise ValueError("physical-readiness validator receipt unlock status changed")


__all__ = [
    "AFKPhysicalReadinessValidatorReceiptV1",
    "AFKPhysicalReadinessValidatorSigner",
    "AFKPhysicalReadinessValidatorTrustStore",
    "IndependentValidatorIdentityV1",
    "VALIDATOR_RECEIPT_SCHEMA",
    "VALIDATION_SCOPE",
    "diagnostic_sha256",
    "validate_afk_physical_readiness_evidence",
    "validator_code_sha256",
    "verify_validator_receipt",
]
