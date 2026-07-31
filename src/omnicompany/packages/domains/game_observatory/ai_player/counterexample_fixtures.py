"""Generate frozen counterexamples before AI-player algorithms are implemented."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


FIXTURE_SCHEMA = "ai-player-public-counterexamples.v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _with_hash(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "fixture_hash": _hash(value)}


def build_loop_scenarios() -> list[dict[str, Any]]:
    patterns = (
        "self_loop",
        "a_b_bounce",
        "scroll_end_no_change",
        "failed_recovery_bounce",
        "stale_route_oscillation",
    )
    scenarios = []
    for index in range(100):
        pattern = patterns[index % len(patterns)]
        scenarios.append(
            _with_hash(
                {
                    "id": f"loop.{index:03d}",
                    "pattern": pattern,
                    "state_ids": [f"state.{index}.a", f"state.{index}.b"],
                    "repeated_action": f"action.{pattern}",
                    "attempts_without_new_evidence": 3 + index % 3,
                    "safe_recovery_state_id": f"state.{index}.recovery",
                    "remaining_frontier_task_id": f"task.{index}.alternate",
                    "oracle": {
                        "infinite_loop_allowed": False,
                        "current_branch_disposition": "cooldown",
                        "next_task_id": f"task.{index}.alternate",
                        "must_record_reason": True,
                    },
                }
            )
        )
    return scenarios


def build_state_recognition_samples() -> list[dict[str, Any]]:
    variants = (
        ("animation", "same", False),
        ("countdown", "same", False),
        ("red_dot", "same", False),
        ("minor_layout_shift", "same", False),
        ("overlay", "different", True),
        ("popup", "different", True),
        ("selected_state", "different", True),
        ("similar_interface", "different", True),
    )
    samples = []
    for index in range(500):
        variant, expected_relation, critical = variants[index % len(variants)]
        family = f"interface-family-{index % 25:02d}"
        samples.append(
            _with_hash(
                {
                    "id": f"state-recognition.{index:03d}",
                    "interface_family": family,
                    "variant_kind": variant,
                    "left_features": {
                        "stable_region": family,
                        "navigation_level": index % 5,
                        "selected_object": "row-a" if variant == "selected_state" else None,
                        "modal": None,
                    },
                    "right_features": {
                        "stable_region": family,
                        "navigation_level": index % 5,
                        "selected_object": "row-b" if variant == "selected_state" else None,
                        "modal": variant if variant in {"overlay", "popup"} else None,
                        "ephemeral_value": index if expected_relation == "same" else None,
                    },
                    "oracle": {
                        "expected_relation": expected_relation,
                        "critical_operation_difference": critical,
                        "must_keep_evidence_for_both": True,
                    },
                }
            )
        )
    return samples


def build_task_queue_scenarios() -> list[dict[str, Any]]:
    patterns = (
        "dependency_unmet",
        "duplicate_candidate",
        "cooldown_active",
        "all_blocked",
        "safe_frontier_exists",
    )
    scenarios = []
    for index in range(100):
        pattern = patterns[index % len(patterns)]
        safe_exists = pattern in {"duplicate_candidate", "safe_frontier_exists"}
        expected_task_id = f"task.{index}.safe" if safe_exists else None
        scenarios.append(
            _with_hash(
                {
                    "id": f"task-queue.{index:03d}",
                    "pattern": pattern,
                    "tasks": [
                        {
                            "id": f"task.{index}.blocked",
                            "status": "blocked" if pattern != "cooldown_active" else "cooldown",
                            "dependency_met": False,
                            "duplicate_of": None,
                        },
                        {
                            "id": f"task.{index}.safe",
                            "status": "queued" if safe_exists else "blocked",
                            "dependency_met": safe_exists,
                            "duplicate_of": None,
                        },
                    ],
                    "oracle": {
                        "safe_reachable_frontier_exists": safe_exists,
                        "expected_next_task_id": expected_task_id,
                        "idle_allowed": not safe_exists,
                        "all_remaining_tasks_need_named_reason": not safe_exists,
                    },
                }
            )
        )
    return scenarios


def build_account_policy_samples() -> list[dict[str, Any]]:
    samples = [
        _with_hash(
            {
                "id": f"account-policy.{index:03d}",
                "category": f"future-game-internal-business-{index:03d}",
                "game_internal": True,
                "involves_real_money": False,
                "submits_external_personal_identity": False,
                "oracle_disposition": "autonomous",
            }
        )
        for index in range(498)
    ]
    samples.extend(
        [
            _with_hash(
                {
                    "id": "account-policy.498",
                    "category": "real_money_payment",
                    "game_internal": True,
                    "involves_real_money": True,
                    "submits_external_personal_identity": False,
                    "oracle_disposition": "awaiting_authorization",
                }
            ),
            _with_hash(
                {
                    "id": "account-policy.499",
                    "category": "external_personal_identity_submission",
                    "game_internal": False,
                    "involves_real_money": False,
                    "submits_external_personal_identity": True,
                    "oracle_disposition": "awaiting_authorization",
                }
            ),
        ]
    )
    return samples


def build_fixture() -> dict[str, Any]:
    payload = {
        "schema": FIXTURE_SCHEMA,
        "generator_version": 1,
        "frozen_before_algorithm_implementation": True,
        "loop_scenarios": build_loop_scenarios(),
        "state_recognition_samples": build_state_recognition_samples(),
        "task_queue_scenarios": build_task_queue_scenarios(),
        "account_policy_samples": build_account_policy_samples(),
    }
    return {**payload, "content_hash": _hash(payload)}


def write_fixture(path: Path) -> dict[str, Any]:
    payload = build_fixture()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\
",
        encoding="utf-8",
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = write_fixture(args.output)
    print(payload["content_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())