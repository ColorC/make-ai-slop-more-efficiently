"""Evaluate the frozen public state counterexamples with production recognition signals."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import StateObservationFeaturesV1
from .counterexample_fixtures import (
    STATE_FIXTURE_SCHEMA_V2,
    STATE_RELATION_COUNTS_V2,
    STATE_VARIANT_POLICY_V2,
)
from .state_recognition import critical_feature_conflicts, state_observation_similarity


_ACCEPTANCE_POLICY: dict[str, float | int] = {
    "same_state_similarity_threshold": 0.90,
    "critical_false_merge_max_count": 0,
    "overall_false_merge_max_rate": 0.01,
    "over_split_max_rate": 0.03,
}
FROZEN_STATE_FIXTURE_V2_CONTENT_HASH = (
    "0a002bb21bd433c3432d9ab6dd663d670a5d91df901108d4b1388c0e72f0475c"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _without_hash(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _validate_fixture(fixture: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if fixture.get("schema") != STATE_FIXTURE_SCHEMA_V2:
        raise ValueError(f"fixture schema must be {STATE_FIXTURE_SCHEMA_V2}")
    if fixture.get("generator_version") != 2:
        raise ValueError("fixture generator_version must be 2")
    if fixture.get("evaluation_policy") != _ACCEPTANCE_POLICY:
        raise ValueError("fixture evaluation_policy does not match the frozen G-02 policy")
    if fixture.get("content_hash") != _hash(_without_hash(fixture, "content_hash")):
        raise ValueError("fixture content_hash does not match its payload")

    samples = fixture.get("state_recognition_samples")
    if not isinstance(samples, list) or len(samples) != 500:
        raise ValueError("fixture must contain exactly 500 state_recognition_samples")
    seen_ids: set[str] = set()
    seen_feature_pairs: set[str] = set()
    variant_counts: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    critical_count = 0
    for index, raw in enumerate(samples):
        if not isinstance(raw, Mapping):
            raise ValueError(f"sample {index} must be an object")
        sample_id = raw.get("id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"sample {index} has no valid id")
        if sample_id in seen_ids:
            raise ValueError(f"duplicate sample id: {sample_id}")
        seen_ids.add(sample_id)
        if raw.get("fixture_hash") != _hash(_without_hash(raw, "fixture_hash")):
            raise ValueError(f"sample fixture_hash mismatch: {sample_id}")
        oracle = raw.get("oracle")
        if not isinstance(oracle, Mapping):
            raise ValueError(f"sample oracle must be an object: {sample_id}")
        if oracle.get("expected_relation") not in {"same", "different"}:
            raise ValueError(f"sample expected_relation is invalid: {sample_id}")
        if not isinstance(oracle.get("critical_operation_difference"), bool):
            raise ValueError(f"sample critical_operation_difference is invalid: {sample_id}")
        left = StateObservationFeaturesV1.model_validate(raw.get("left_features"))
        right = StateObservationFeaturesV1.model_validate(raw.get("right_features"))
        pair_sides = sorted(
            (
                _canonical_json(left.model_dump(mode="json", by_alias=True)),
                _canonical_json(right.model_dump(mode="json", by_alias=True)),
            )
        )
        pair_hash = _hash(pair_sides)
        if pair_hash in seen_feature_pairs:
            raise ValueError(f"duplicate feature pair content: {sample_id}")
        seen_feature_pairs.add(pair_hash)

        variant = raw.get("variant_kind")
        if not isinstance(variant, str) or variant not in STATE_VARIANT_POLICY_V2:
            raise ValueError(f"sample variant_kind is invalid: {sample_id}")
        expected_category = STATE_VARIANT_POLICY_V2[variant]
        if oracle.get("expected_relation") != expected_category["expected_relation"]:
            raise ValueError(f"sample relation does not match variant category: {sample_id}")
        if (
            oracle.get("critical_operation_difference")
            != expected_category["critical_operation_difference"]
        ):
            raise ValueError(f"sample critical category does not match variant: {sample_id}")
        variant_counts[variant] += 1
        relation_counts[oracle["expected_relation"]] += 1
        critical_count += int(oracle["critical_operation_difference"])

    expected_variant_counts = {
        variant: int(policy["expected_count"])
        for variant, policy in STATE_VARIANT_POLICY_V2.items()
    }
    if dict(variant_counts) != expected_variant_counts:
        raise ValueError("fixture variant distribution does not match the frozen v2 corpus")
    if dict(relation_counts) != {
        "same": STATE_RELATION_COUNTS_V2["same"],
        "different": STATE_RELATION_COUNTS_V2["different"],
    }:
        raise ValueError("fixture relation distribution does not match the frozen v2 corpus")
    if critical_count != STATE_RELATION_COUNTS_V2["critical"]:
        raise ValueError("fixture critical distribution does not match the frozen v2 corpus")
    return samples


def evaluate_state_counterexamples(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate structurally valid test data without asserting production provenance."""
    samples = _validate_fixture(fixture)
    threshold = float(_ACCEPTANCE_POLICY["same_state_similarity_threshold"])
    results: list[dict[str, Any]] = []
    correct_count = 0
    expected_same_count = 0
    expected_different_count = 0
    critical_expected_count = 0
    critical_false_merge_count = 0
    false_merge_count = 0
    over_split_count = 0
    fixture_contract_violation_count = 0

    for raw in samples:
        left = StateObservationFeaturesV1.model_validate(raw["left_features"])
        right = StateObservationFeaturesV1.model_validate(raw["right_features"])
        direct_conflicts = critical_feature_conflicts(left, right)
        similarity, similarity_conflicts = state_observation_similarity(left, right)
        if direct_conflicts != similarity_conflicts:
            raise RuntimeError(f"production conflict functions disagree for sample {raw['id']}")
        predicted_relation = (
            "same" if not direct_conflicts and similarity >= threshold else "different"
        )
        oracle = raw["oracle"]
        expected_relation = oracle["expected_relation"]
        critical_expected = oracle["critical_operation_difference"]
        correct = predicted_relation == expected_relation
        error_kind: str | None = None
        if expected_relation == "same":
            expected_same_count += 1
            if predicted_relation == "different":
                over_split_count += 1
                error_kind = "over_split"
        else:
            expected_different_count += 1
            if predicted_relation == "same":
                false_merge_count += 1
                error_kind = "false_merge"
        if critical_expected:
            critical_expected_count += 1
            if not direct_conflicts:
                fixture_contract_violation_count += 1
            if predicted_relation == "same":
                critical_false_merge_count += 1
                error_kind = "critical_false_merge"
        elif direct_conflicts:
            fixture_contract_violation_count += 1
        if correct:
            correct_count += 1
        results.append(
            {
                "id": raw["id"],
                "variant_kind": raw["variant_kind"],
                "expected_relation": expected_relation,
                "predicted_relation": predicted_relation,
                "similarity": round(similarity, 8),
                "critical_conflicts": list(direct_conflicts),
                "correct": correct,
                "error_kind": error_kind,
            }
        )

    sample_count = len(results)
    if sample_count == 0 or expected_different_count == 0 or expected_same_count == 0:
        raise ValueError("state benchmark metric denominator must be non-zero")
    false_merge_rate = false_merge_count / expected_different_count
    over_split_rate = over_split_count / expected_same_count
    metrics = {
        "sample_count": sample_count,
        "correct_count": correct_count,
        "accuracy": correct_count / sample_count,
        "expected_same_count": expected_same_count,
        "expected_different_count": expected_different_count,
        "critical_expected_count": critical_expected_count,
        "critical_false_merge_count": critical_false_merge_count,
        "overall_false_merge_count": false_merge_count,
        "overall_false_merge_rate": false_merge_rate,
        "over_split_count": over_split_count,
        "over_split_rate": over_split_rate,
        "fixture_contract_violation_count": fixture_contract_violation_count,
    }
    gates = {
        "exactly_500_samples": sample_count == 500,
        "critical_false_merge_within_limit": critical_false_merge_count
        <= int(_ACCEPTANCE_POLICY["critical_false_merge_max_count"]),
        "overall_false_merge_within_limit": false_merge_rate
        <= float(_ACCEPTANCE_POLICY["overall_false_merge_max_rate"]),
        "over_split_within_limit": over_split_rate
        <= float(_ACCEPTANCE_POLICY["over_split_max_rate"]),
        "fixture_operation_contracts_valid": fixture_contract_violation_count == 0,
    }
    payload = {
        "schema": "ai-player-public-state-counterexample-result.v2",
        "fixture_schema": fixture["schema"],
        "fixture_content_hash": fixture["content_hash"],
        "trust_root": {
            "frozen_content_hash": FROZEN_STATE_FIXTURE_V2_CONTENT_HASH,
            "fixture_matches_frozen": (
                fixture["content_hash"] == FROZEN_STATE_FIXTURE_V2_CONTENT_HASH
            ),
        },
        "evaluator": {
            "implementation": ("state_observation_similarity+critical_feature_conflicts"),
            "policy": _ACCEPTANCE_POLICY,
            "rate_denominators": {
                "overall_false_merge_rate": "expected_different_count",
                "over_split_rate": "expected_same_count",
            },
        },
        "status": "PASS" if all(gates.values()) else "FAIL",
        "metrics": metrics,
        "gates": gates,
        "sample_results": results,
    }
    return {**payload, "result_hash": _hash(payload)}


def evaluate_file(
    fixture_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Evaluate the production corpus only after matching its frozen trust root."""
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("content_hash") != FROZEN_STATE_FIXTURE_V2_CONTENT_HASH:
        raise ValueError("fixture content_hash does not match the frozen production trust root")
    result = evaluate_state_counterexamples(fixture)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = evaluate_file(args.fixture, args.output)
    print(json.dumps({"status": result["status"], "metrics": result["metrics"]}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
