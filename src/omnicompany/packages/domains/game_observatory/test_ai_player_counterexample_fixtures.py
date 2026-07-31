from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.account_policy import (
    AccountActionIntentV1,
    evaluate_account_action,
)
from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    AccountActionPolicyV1,
    EvidenceReferenceV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.counterexample_fixtures import (
    build_fixture,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = (
    REPO_ROOT
    / "data"
    / "domains"
    / "game_observatory"
    / "benchmarks"
    / "ai_player"
    / "fixtures"
    / "public_counterexamples.v1.json"
)


def test_committed_public_counterexamples_match_the_pre_algorithm_generator():
    committed = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    generated = build_fixture()

    assert committed == generated
    assert len(committed["loop_scenarios"]) == 100
    assert len(committed["state_recognition_samples"]) == 500
    assert len(committed["task_queue_scenarios"]) == 100
    assert len(committed["account_policy_samples"]) == 500


def test_state_samples_cover_ephemeral_and_operation_changing_variants():
    fixture = build_fixture()
    variants = Counter(
        item["variant_kind"] for item in fixture["state_recognition_samples"]
    )

    assert set(variants) == {
        "animation",
        "countdown",
        "red_dot",
        "minor_layout_shift",
        "overlay",
        "popup",
        "selected_state",
        "similar_interface",
    }
    assert min(variants.values()) >= 62
    assert all(
        item["oracle"]["expected_relation"] == "different"
        for item in fixture["state_recognition_samples"]
        if item["variant_kind"] in {"overlay", "popup", "selected_state", "similar_interface"}
    )


def test_loop_and_task_oracles_never_idle_when_an_alternate_safe_frontier_exists():
    fixture = build_fixture()

    assert all(
        item["oracle"]["infinite_loop_allowed"] is False
        and item["oracle"]["current_branch_disposition"] == "cooldown"
        and item["oracle"]["next_task_id"]
        for item in fixture["loop_scenarios"]
    )
    assert all(
        item["oracle"]["expected_next_task_id"] is not None
        and item["oracle"]["idle_allowed"] is False
        for item in fixture["task_queue_scenarios"]
        if item["oracle"]["safe_reachable_frontier_exists"]
    )


def test_all_500_account_policy_oracles_match_the_runtime_evaluator():
    fixture = build_fixture()
    environment_id = "environment.sanguo.policy-fixture"
    policy = AccountActionPolicyV1(
        id="policy.sanguo.fixture",
        environment_id=environment_id,
        evidence_refs=[
            EvidenceReferenceV1(
                environment_id=environment_id,
                source_ids=["fixture:account-policy.v1"],
            )
        ],
        ai_identity_label="三谋 AI 玩家",
    )

    for sample in fixture["account_policy_samples"]:
        decision = evaluate_account_action(
            AccountActionIntentV1(
                id=sample["id"],
                category=sample["category"],
                summary=sample["category"],
                game_internal=sample["game_internal"],
                involves_real_money=sample["involves_real_money"],
                submits_external_personal_identity=sample[
                    "submits_external_personal_identity"
                ],
            ),
            policy,
        )
        assert decision.disposition == sample["oracle_disposition"]