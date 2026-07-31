"""Fail-closed bootstrap gate for real-device AI-player actions.

The gate never trusts a persisted benchmark result.  It re-hashes the detached
benchmark inputs and invokes the canonical AFK interaction-preflight evaluator;
an in-process cache is used only after those hashes and the producer code hash
have been rechecked.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .afk_interaction_preflight_benchmark import (
    BENCHMARK_ID,
    _producer_code_sha256,
    evaluate_benchmark,
)
from .afk_physical_readiness_validator import (
    AFKPhysicalReadinessValidatorReceiptV1,
    validate_afk_physical_readiness_evidence,
    verify_validator_receipt,
)
from .contracts import EnvironmentScopeV1


CONFIG_ENVIRONMENT_VARIABLE = "OMNICOMPANY_AI_PLAYER_PHYSICAL_READINESS_CONFIG"
CONFIG_SCHEMA = "game-observatory.ai-player.physical-readiness-config.v1"
CONFIG_SCHEMA_V2 = "game-observatory.ai-player.physical-readiness-config.v2"
GATE_SCHEMA = "game-observatory.ai-player.physical-readiness-gate.v1"
NON_PHYSICAL_CHANNELS = frozenset({"fixture", "test"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DetachedBenchmarkInputV1(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PhysicalReadinessBenchmarkConfigV1(_StrictModel):
    schema_id: Literal[CONFIG_SCHEMA] = Field(default=CONFIG_SCHEMA, alias="schema")
    repository_root: str = Field(min_length=1)
    fixture: DetachedBenchmarkInputV1
    labels: DetachedBenchmarkInputV1
    attestation: DetachedBenchmarkInputV1
    predictions: DetachedBenchmarkInputV1


class AFKPhysicalReadinessEvidenceV2(_StrictModel):
    acceptance_manifest: DetachedBenchmarkInputV1
    candidate_manifest: DetachedBenchmarkInputV1
    candidate_validation: DetachedBenchmarkInputV1
    adjudication_evidence: DetachedBenchmarkInputV1
    truth_audit_result: DetachedBenchmarkInputV1
    interaction_preflight_result: DetachedBenchmarkInputV1


class TrustedPhysicalReadinessValidatorV1(_StrictModel):
    kind: Literal["independent_machine_validator"] = "independent_machine_validator"
    id: str = Field(min_length=1)
    public_key_base64: str = Field(min_length=1)
    status: Literal["trusted_for_eligibility_diagnostics"] = (
        "trusted_for_eligibility_diagnostics"
    )


class PhysicalReadinessBenchmarkConfigV2(_StrictModel):
    schema_id: Literal[CONFIG_SCHEMA_V2] = Field(default=CONFIG_SCHEMA_V2, alias="schema")
    repository_root: str = Field(min_length=1)
    expected_afk_build_id: str = Field(min_length=1)
    evidence: AFKPhysicalReadinessEvidenceV2
    validator_receipt: DetachedBenchmarkInputV1
    trusted_validators: tuple[TrustedPhysicalReadinessValidatorV1, ...] = Field(
        min_length=1
    )


class PhysicalReadinessGateV1(_StrictModel):
    schema_id: Literal[GATE_SCHEMA] = Field(default=GATE_SCHEMA, alias="schema")
    status: Literal["ready", "blocked", "bypassed"]
    physical_play_unlocked: bool
    environment_id: str = Field(min_length=1)
    environment_channel: str = Field(min_length=1)
    benchmark_id: Literal[BENCHMARK_ID] = BENCHMARK_ID
    benchmark_verdict: Literal["PASS", "FAIL"] | None = None
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evaluated_at: str | None = None
    input_sha256: dict[str, str] = Field(default_factory=dict)
    gaps: list[Any] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ResolvedBenchmarkConfig:
    repository_root: Path
    fixture_path: Path
    expected_fixture_sha256: str
    labels_path: Path
    expected_labels_sha256: str
    attestation_path: Path
    expected_attestation_sha256: str
    predictions_path: Path
    expected_predictions_sha256: str


@dataclass(frozen=True, slots=True)
class _ResolvedEvidenceConfig:
    repository_root: Path
    expected_afk_build_id: str
    evidence: dict[str, tuple[Path, str]]
    validator_receipt_path: Path
    expected_validator_receipt_sha256: str
    trusted_validator_public_keys: dict[str, str]


_CACHE_LOCK = Lock()
_EVALUATION_CACHE: dict[tuple[str, ...], dict[str, Any]] = {}


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


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not _inside(root, resolved):
        raise ValueError("benchmark input must remain inside repository_root")
    return resolved


def _resolve_config(
    config: PhysicalReadinessBenchmarkConfigV1,
    *,
    config_path: Path,
) -> _ResolvedBenchmarkConfig:
    repository_value = Path(config.repository_root)
    repository_root = (
        repository_value
        if repository_value.is_absolute()
        else config_path.parent / repository_value
    ).resolve()
    if not repository_root.is_dir():
        raise ValueError("physical readiness repository_root is not a directory")
    return _ResolvedBenchmarkConfig(
        repository_root=repository_root,
        fixture_path=_resolve_path(repository_root, config.fixture.path),
        expected_fixture_sha256=config.fixture.sha256,
        labels_path=_resolve_path(repository_root, config.labels.path),
        expected_labels_sha256=config.labels.sha256,
        attestation_path=_resolve_path(repository_root, config.attestation.path),
        expected_attestation_sha256=config.attestation.sha256,
        predictions_path=_resolve_path(repository_root, config.predictions.path),
        expected_predictions_sha256=config.predictions.sha256,
    )


def _resolve_evidence_config(
    config: PhysicalReadinessBenchmarkConfigV2,
    *,
    config_path: Path,
) -> _ResolvedEvidenceConfig:
    repository_value = Path(config.repository_root)
    repository_root = (
        repository_value
        if repository_value.is_absolute()
        else config_path.parent / repository_value
    ).resolve()
    if not repository_root.is_dir():
        raise ValueError("physical readiness repository_root is not a directory")
    evidence = {
        name: (
            _resolve_path(repository_root, reference.path),
            reference.sha256,
        )
        for name in config.evidence.__class__.model_fields
        for reference in (getattr(config.evidence, name),)
    }
    trusted = {
        item.id: item.public_key_base64 for item in config.trusted_validators
    }
    if len(trusted) != len(config.trusted_validators):
        raise ValueError("physical readiness validator ids must be unique")
    return _ResolvedEvidenceConfig(
        repository_root=repository_root,
        expected_afk_build_id=config.expected_afk_build_id,
        evidence=evidence,
        validator_receipt_path=_resolve_path(
            repository_root, config.validator_receipt.path
        ),
        expected_validator_receipt_sha256=config.validator_receipt.sha256,
        trusted_validator_public_keys=trusted,
    )


def _configured_registry_path() -> Path | None:
    configured = os.environ.get(CONFIG_ENVIRONMENT_VARIABLE, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    repository_root = Path(__file__).resolve().parents[6]
    fixed_registry = (
        repository_root
        / "config"
        / "game_observatory"
        / "ai_player_physical_readiness.v1.json"
    )
    return fixed_registry if fixed_registry.is_file() else None


def load_physical_readiness_config() -> (
    _ResolvedBenchmarkConfig | _ResolvedEvidenceConfig | None
):
    """Load the explicit environment/fixed registry without reading result JSON."""

    config_path = _configured_registry_path()
    if config_path is None:
        return None
    if not config_path.is_file():
        raise ValueError("physical readiness config path does not exist")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("physical readiness config must be a JSON object")
    schema = raw.get("schema")
    if schema == CONFIG_SCHEMA:
        parsed_v1 = PhysicalReadinessBenchmarkConfigV1.model_validate(raw)
        return _resolve_config(parsed_v1, config_path=config_path)
    if schema == CONFIG_SCHEMA_V2:
        parsed_v2 = PhysicalReadinessBenchmarkConfigV2.model_validate(raw)
        return _resolve_evidence_config(parsed_v2, config_path=config_path)
    raise ValueError(f"unsupported physical readiness config schema: {schema}")


class PhysicalReadinessEvaluator:
    """Evaluate one environment against the AFK bootstrap benchmark."""

    @staticmethod
    def _gate(
        environment: EnvironmentScopeV1,
        *,
        status: Literal["ready", "blocked", "bypassed"],
        unlocked: bool,
        reason_code: str,
        reason: str,
        benchmark_verdict: Literal["PASS", "FAIL"] | None = None,
        evaluated_at: str | None = None,
        input_sha256: dict[str, str] | None = None,
        gaps: list[Any] | None = None,
    ) -> PhysicalReadinessGateV1:
        return PhysicalReadinessGateV1(
            status=status,
            physical_play_unlocked=unlocked,
            environment_id=environment.id,
            environment_channel=environment.channel,
            benchmark_verdict=benchmark_verdict,
            reason_code=reason_code,
            reason=reason,
            evaluated_at=evaluated_at,
            input_sha256=input_sha256 or {},
            gaps=gaps or [],
        )

    def _evaluate_evidence_config(
        self,
        environment: EnvironmentScopeV1,
        config: _ResolvedEvidenceConfig,
    ) -> PhysicalReadinessGateV1:
        missing_evidence = sorted(
            name for name, (path, _) in config.evidence.items() if not path.is_file()
        )
        if missing_evidence:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_input_unreadable",
                reason="AFK 实体门证据输入缺失：" + ", ".join(missing_evidence) + "。",
                gaps=[
                    {
                        "code": "benchmark_evidence_input_missing",
                        "input_names": missing_evidence,
                    }
                ],
            )
        if not config.validator_receipt_path.is_file():
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_validator_receipt_missing",
                reason="AFK 实体门缺少独立机器验证收据。",
                gaps=[{"code": "independent_validator_receipt_missing"}],
            )

        direct_hashes = {
            name: _sha256_file(path)
            for name, (path, _) in sorted(config.evidence.items())
        }
        mismatches = sorted(
            name
            for name, actual in direct_hashes.items()
            if actual != config.evidence[name][1]
        )
        if mismatches:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_input_hash_mismatch",
                reason="AFK 实体门证据输入哈希不匹配：" + ", ".join(mismatches) + "。",
                input_sha256=direct_hashes,
                gaps=[
                    {
                        "code": "benchmark_evidence_hash_mismatch",
                        "input_names": mismatches,
                    }
                ],
            )
        receipt_hash = _sha256_file(config.validator_receipt_path)
        if receipt_hash != config.expected_validator_receipt_sha256:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_validator_receipt_hash_mismatch",
                reason="AFK 实体门独立验证收据哈希不匹配。",
                input_sha256={**direct_hashes, "validator_receipt": receipt_hash},
                gaps=[{"code": "independent_validator_receipt_hash_mismatch"}],
            )

        try:
            diagnostic = validate_afk_physical_readiness_evidence(
                repository_root=config.repository_root,
                expected_build_id=config.expected_afk_build_id,
                detached_inputs=config.evidence,
            )
        except Exception as exc:
            reason_code = (
                "benchmark_input_hash_mismatch"
                if "hash mismatch" in str(exc).lower()
                else "benchmark_evidence_validation_failed"
            )
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code=reason_code,
                reason=f"AFK 实体门证据包无法完整重放验证：{exc}",
                input_sha256={**direct_hashes, "validator_receipt": receipt_hash},
                gaps=[
                    {
                        "code": (
                            "benchmark_nested_evidence_hash_mismatch"
                            if reason_code == "benchmark_input_hash_mismatch"
                            else "benchmark_evidence_validation_failed"
                        ),
                        "detail": str(exc),
                    }
                ],
            )

        try:
            receipt = AFKPhysicalReadinessValidatorReceiptV1.model_validate_json(
                config.validator_receipt_path.read_bytes()
            )
            verify_validator_receipt(
                receipt=receipt,
                diagnostic=diagnostic,
                trusted_public_keys=config.trusted_validator_public_keys,
            )
        except Exception as exc:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_validator_receipt_invalid",
                reason=f"AFK 实体门独立验证收据无效：{exc}",
                input_sha256={
                    **dict(diagnostic.get("input_sha256") or direct_hashes),
                    "validator_receipt": receipt_hash,
                },
                gaps=[
                    {
                        "code": "independent_validator_receipt_invalid",
                        "detail": str(exc),
                    }
                ],
            )

        passed = (
            diagnostic.get("benchmark_id") == BENCHMARK_ID
            and diagnostic.get("verdict") == "PASS"
            and diagnostic.get("physical_play_unlocked") is True
        )
        return self._gate(
            environment,
            status="ready" if passed else "blocked",
            unlocked=passed,
            reason_code="benchmark_passed" if passed else "benchmark_failed",
            reason=(
                "AFK 实体门证据包与独立验证收据均已重放通过。"
                if passed
                else "AFK 实体门证据包已完整装载并验证，但仍有明确资格缺口。"
            ),
            benchmark_verdict=("PASS" if passed else "FAIL"),
            evaluated_at=receipt.issued_at.isoformat(),
            input_sha256={
                **dict(diagnostic["input_sha256"]),
                "validator_receipt": receipt_hash,
            },
            gaps=list(diagnostic.get("gaps") or []),
        )

    def evaluate(self, environment: EnvironmentScopeV1) -> PhysicalReadinessGateV1:
        channel = environment.channel.strip().lower()
        if channel in NON_PHYSICAL_CHANNELS:
            return self._gate(
                environment,
                status="bypassed",
                unlocked=True,
                reason_code="non_physical_test_environment",
                reason="fixture/test 环境显式旁路实体游玩基准。",
            )

        try:
            config = load_physical_readiness_config()
        except Exception as exc:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_configuration_invalid",
                reason=f"实体游玩基准配置无效：{exc}",
            )
        if config is None:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_configuration_missing",
                reason="尚未配置可重放的 AFK 已知控件预检基准。",
            )

        if isinstance(config, _ResolvedEvidenceConfig):
            return self._evaluate_evidence_config(environment, config)

        paths = {
            "fixture": (config.fixture_path, config.expected_fixture_sha256),
            "labels": (config.labels_path, config.expected_labels_sha256),
            "attestation": (
                config.attestation_path,
                config.expected_attestation_sha256,
            ),
            "predictions": (
                config.predictions_path,
                config.expected_predictions_sha256,
            ),
        }
        try:
            actual_hashes = {name: _sha256_file(path) for name, (path, _) in paths.items()}
        except Exception as exc:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_input_unreadable",
                reason=f"实体游玩基准输入无法读取：{exc}",
            )
        mismatches = sorted(
            name
            for name, actual in actual_hashes.items()
            if actual != paths[name][1]
        )
        if mismatches:
            return self._gate(
                environment,
                status="blocked",
                unlocked=False,
                reason_code="benchmark_input_hash_mismatch",
                reason=f"实体游玩基准输入哈希不匹配：{', '.join(mismatches)}。",
                input_sha256=actual_hashes,
            )

        producer_code_sha256 = _producer_code_sha256()
        cache_key = (
            str(config.repository_root),
            actual_hashes["fixture"],
            actual_hashes["labels"],
            actual_hashes["attestation"],
            actual_hashes["predictions"],
            producer_code_sha256,
        )
        with _CACHE_LOCK:
            result = _EVALUATION_CACHE.get(cache_key)
        if result is None:
            try:
                result = evaluate_benchmark(
                    repository_root=config.repository_root,
                    fixture_path=config.fixture_path,
                    expected_fixture_sha256=config.expected_fixture_sha256,
                    labels_path=config.labels_path,
                    expected_labels_sha256=config.expected_labels_sha256,
                    attestation_path=config.attestation_path,
                    expected_attestation_sha256=config.expected_attestation_sha256,
                    predictions_path=config.predictions_path,
                    expected_predictions_sha256=config.expected_predictions_sha256,
                )
            except Exception as exc:
                return self._gate(
                    environment,
                    status="blocked",
                    unlocked=False,
                    reason_code="benchmark_replay_validation_failed",
                    reason=f"AFK 已知控件基准无法完整重放验证：{exc}",
                    input_sha256=actual_hashes,
                )
            with _CACHE_LOCK:
                _EVALUATION_CACHE[cache_key] = json.loads(json.dumps(result))

        passed = (
            result.get("benchmark_id") == BENCHMARK_ID
            and result.get("verdict") == "PASS"
            and result.get("physical_play_unlocked") is True
        )
        verdict = "PASS" if result.get("verdict") == "PASS" else "FAIL"
        return self._gate(
            environment,
            status="ready" if passed else "blocked",
            unlocked=passed,
            reason_code="benchmark_passed" if passed else "benchmark_failed",
            reason=(
                "AFK 已知控件预检基准已完整可重放验证通过。"
                if passed
                else "AFK 已知控件预检基准当前未通过，实体游玩保持阻断。"
            ),
            benchmark_verdict=verdict,
            evaluated_at=str(result.get("evaluated_at") or "") or None,
            input_sha256=actual_hashes,
            gaps=list(result.get("gaps") or []),
        )


__all__ = [
    "AFKPhysicalReadinessEvidenceV2",
    "CONFIG_ENVIRONMENT_VARIABLE",
    "DetachedBenchmarkInputV1",
    "PhysicalReadinessBenchmarkConfigV1",
    "PhysicalReadinessBenchmarkConfigV2",
    "PhysicalReadinessEvaluator",
    "PhysicalReadinessGateV1",
    "TrustedPhysicalReadinessValidatorV1",
    "load_physical_readiness_config",
]
