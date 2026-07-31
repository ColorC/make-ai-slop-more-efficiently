from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from omnicompany.packages.domains.game_observatory.ai_player.external_agent_continuity import (
    ExternalAgentContinuousSessionV1,
    PlayerFacilityContractV1,
    build_afkj_external_agent_manifest,
    build_player_facility_contract,
    check_external_agent_contracts,
    facility_contract_sha256,
    render_facility_help,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_facility_contract_is_a_single_guarded_public_cli() -> None:
    contract = build_player_facility_contract()

    assert contract.public_root == "omni game player"
    assert contract.facility_contract_sha256 == facility_contract_sha256(contract)
    assert len(contract.commands) == 54
    assert len({item.id for item in contract.commands}) == len(contract.commands)
    assert all(item.path.startswith("omni game player ") for item in contract.commands)
    assert all(item.json_output for item in contract.commands)

    guarded = [
        item for item in contract.commands if item.mutation_scope in {"device", "account"}
    ]
    assert guarded
    assert all(
        item.guard_profile in {"device_preflight", "account_policy"} for item in guarded
    )
    assert [item.tier for item in contract.agent_layers] == ["A0", "A1", "A2", "A3", "A4"]
    assert {item.provider for item in contract.external_agent_providers} == {
        "codex-cli",
        "claude-code-cli",
    }


def test_facility_hash_rejects_a_tampered_copy() -> None:
    payload = build_player_facility_contract().model_dump(mode="json", by_alias=True)
    tampered = deepcopy(payload)
    tampered["invariants"][0] = "被篡改的守卫规则"

    with pytest.raises(ValidationError, match="sha256"):
        PlayerFacilityContractV1.model_validate(tampered)


def test_continuous_session_allows_resume_but_not_an_unexplained_replacement() -> None:
    common = {
        "id": "external-session.afkj.1",
        "provider": "codex-cli",
        "model_selector": "gpt-5.6-terra",
        "requested_effort": "medium",
        "actual_effort": "medium",
        "external_session_id": "019f0000-0000-7000-8000-000000000001",
        "environment_id": "environment.afkj.known-truth",
        "phase_id": "EA-3.B5",
        "facility_contract_sha256": "a" * 64,
        "semantic_action_count": 30,
        "atomic_action_count": 42,
        "observation_count": 31,
        "heartbeat_sequence": 30,
        "task_ids": ["benchmark.B5"],
        "started_at": "2026-07-17T09:00:00+08:00",
        "last_heartbeat_at": "2026-07-17T09:10:00+08:00",
    }
    first = ExternalAgentContinuousSessionV1.model_validate(common)
    assert first.generation == 1
    assert first.semantic_action_count == 30

    unexplained = {**common, "generation": 2}
    with pytest.raises(ValidationError, match="replacement generation"):
        ExternalAgentContinuousSessionV1.model_validate(unexplained)

    replacement = ExternalAgentContinuousSessionV1.model_validate(
        {
            **common,
            "id": "external-session.afkj.2",
            "generation": 2,
            "previous_session_id": first.id,
            "restart_reason": "phase_complete",
        }
    )
    assert replacement.restart_reason == "phase_complete"


def test_afkj_manifest_freezes_candidates_tasks_and_quality_first_thresholds() -> None:
    manifest = build_afkj_external_agent_manifest()

    assert [item.id for item in manifest.candidates] == [
        "claude-sonnet-5-medium",
        "gpt-5.6-terra-medium",
        "gpt-5.6-luna-medium",
    ]
    assert {item.requested_effort for item in manifest.candidates} == {"medium"}
    assert [(item.id, item.sample_count) for item in manifest.tasks] == [
        ("B0", 10),
        ("B1", 20),
        ("B2", 8),
        ("B3", 10),
        ("B4", 3),
        ("B5", 30),
    ]
    assert manifest.tasks[-1].same_session_required
    assert manifest.thresholds.known_target_accuracy_min == 0.95
    assert manifest.thresholds.overlay_lower_layer_misclick_max == 0
    assert manifest.thresholds.evidence_completeness_min == 1
    assert manifest.thresholds.a1_warm_decision_p95_seconds_max == 30
    assert manifest.thresholds.a2_warm_decision_p95_seconds_max == 60
    assert "先通过全部质量硬门" in manifest.selection_rule


def test_committed_contracts_and_help_are_generated_from_the_same_source() -> None:
    paths = check_external_agent_contracts(REPOSITORY_ROOT)

    assert len(paths) == 3
    help_path = next(path for path in paths if path.suffix == ".txt")
    raw = help_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8") == render_facility_help()
    assert "session resume" in raw.decode("utf-8")
    assert "每个游戏动作创建一个新 Agent" not in raw.decode("utf-8")