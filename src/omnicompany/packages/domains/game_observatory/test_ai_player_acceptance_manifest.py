from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = (
    REPO_ROOT
    / "data"
    / "domains"
    / "game_observatory"
    / "benchmarks"
    / "ai_player"
    / "acceptance_manifest.v1.json"
)


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_ai_player_manifest_keeps_exact_final_exit_counts():
    manifest = load_manifest()

    assert manifest["schema"] == "ai-player-acceptance-manifest.v1"
    assert manifest["final_exit"] == {
        "requirements_passed": "11/11",
        "products_passed": "13/13",
        "e2e_passed": "10/10",
        "gates_passed": "12/12",
        "blocker_count": 0,
        "major_count": 0,
        "clean_database_rerun": "PASS",
        "independent_review": "PASS",
    }


def test_candidate_truth_cannot_be_reported_as_frozen_or_passed():
    manifest = load_manifest()
    afk = next(item for item in manifest["targets"] if item["benchmark_id"] == "afk_hero_growth_v1")

    assert manifest["freeze_status"] == "candidate_review_required"
    assert manifest["manifest_pass"] is False
    assert manifest["blocking_reasons"]
    assert all(value == 0 for value in afk["frozen_counts"].values())
    assert afk["candidate_counts"]["readable_artifacts"] == 592
    assert afk["candidate_counts"]["semantic_states"] >= afk["required_before_freeze"]["semantic_states_at_least"]
    assert afk["candidate_counts"]["safe_edge_candidates"] >= afk["required_before_freeze"]["safe_transition_edges_at_least"]


def test_dual_game_roles_and_sanguo_account_boundary_are_explicit():
    manifest = load_manifest()
    targets = {item["benchmark_id"]: item for item in manifest["targets"]}

    assert set(targets) == {"afk_hero_growth_v1", "sanguo_mouding_pure_ai_v1"}
    assert targets["afk_hero_growth_v1"]["role"] == "known_truth_regression"

    sanguo = targets["sanguo_mouding_pure_ai_v1"]
    assert sanguo["role"] == "live_business_autonomous_operation"
    assert sanguo["dynamic_requirements"]["consecutive_natural_days"] == 7
    assert sanguo["dynamic_requirements"]["user_gameplay_actions_during_campaign"] == 0
    assert "normal_in_game_communication" in sanguo["account_policy"]["autonomous_in_game_actions"]
    assert set(sanguo["account_policy"]["separate_authorization_actions"]) == {
        "real_money_payment",
        "external_personal_identity_submission",
    }


def test_manifest_references_existing_local_inputs():
    manifest = load_manifest()

    for target in manifest["targets"]:
        for key in ("candidate_inventory", "environment_baseline"):
            relative_path = target.get(key)
            if relative_path:
                assert (REPO_ROOT / relative_path).is_file(), relative_path