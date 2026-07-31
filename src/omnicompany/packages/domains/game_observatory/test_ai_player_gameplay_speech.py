from __future__ import annotations

import hashlib

import pytest

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    AccountActionPolicyV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    GameplayCandidateV1,
    SemanticStateV1,
    SpeechEventV1,
    SpeechIntentV1,
    TransitionEdgeV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    EvidenceRun,
    EvidenceStep,
    NormalizedAction,
    RunResult,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


ENVIRONMENT_ID = "environment.gameplay-speech.fixture"


def initialized_store(tmp_path):
    observatory = ObservatoryStore(tmp_path / "observatory")
    raw = b"gameplay-speech-fixture"
    path = observatory.artifact_root / "fixture.bin"
    path.write_bytes(raw)
    artifact = ArtifactRef(
        id="artifact.gameplay-speech.fixture",
        kind="screenshot",
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        metadata={"environment_id": ENVIRONMENT_ID},
    )
    observatory.save_artifact(artifact)
    evidence = EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=[artifact.id],
    )
    player = AIPlayerStore(observatory)
    player.put_environment(
        EnvironmentScopeV1(
            id=ENVIRONMENT_ID,
            game_id="fixture-game",
            build_scope_id="build.fixture",
            account_scope_id="account.fixture.ai",
            channel="fixture",
            device_scope_id="device.fixture",
            locale="zh-CN",
            viewport_width=1280,
            viewport_height=720,
            identity_hash="identity-hash-gameplay-speech-fixture",
            evidence_refs=[evidence],
        )
    )
    task = player.enqueue_task(
        FrontierTaskV1(
            id="task.gameplay-speech.fixture",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[evidence],
            title="识别一个玩法边界",
            source="coverage_gap",
            reason="已有入口证据，需要闭合玩法状态和出口。",
            value_score=5,
            expected_coverage_gain=5,
            action_budget=2,
            time_budget_seconds=60,
        )
    )
    before = player.put_semantic_state(
        SemanticStateV1(
            id="state.gameplay.entry",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[evidence],
            title="玩法入口",
            description="进入玩法前的稳定状态。",
            semantic_fingerprint="gameplay-entry",
            observation_feature_hashes=["feature.gameplay.entry"],
            status="accepted",
        )
    )
    main = player.put_semantic_state(
        SemanticStateV1(
            id="state.gameplay.main",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[evidence],
            title="玩法主界面",
            description="玩法规则与进度可见的主状态。",
            semantic_fingerprint="gameplay-main",
            observation_feature_hashes=["feature.gameplay.main"],
            status="accepted",
        )
    )
    edge = player.put_transition_edge(
        TransitionEdgeV1(
            id="edge.gameplay.open",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[evidence],
            from_state_id=before.id,
            to_state_id=main.id,
            action=NormalizedAction(type="tap", x=400, y=300),
            expected_change="进入玩法主界面",
            observed_change="玩法主界面稳定显示",
            outcome="verified_transition",
        )
    )
    return observatory, player, evidence, task, before, main, edge


def test_gameplay_candidate_is_versioned_and_resolves_every_canonical_boundary(tmp_path):
    _observatory, player, evidence, task, before, main, edge = initialized_store(tmp_path)
    candidate = GameplayCandidateV1(
        id="gameplay.fixture.business",
        environment_id=ENVIRONMENT_ID,
        evidence_refs=[evidence],
        game_id="fixture-game",
        title="示例玩法",
        triggering_task_ids=[task.id],
        entry_state_ids=[before.id],
        main_state_ids=[main.id],
        transition_edge_ids=[edge.id],
        rule_clues=["点击入口后进入主界面，并显示一组可操作规则。"],
        resource_or_progression_clues=["主界面展示当前进度。"],
        exit_state_ids=[before.id],
        boundary_summary="入口、主界面和返回出口均由 canonical 状态与转移边支持。",
    )
    assert player.append_gameplay_candidate(candidate) == candidate
    successor = candidate.model_copy(
        update={
            "version": 2,
            "status": "scope_review",
            "boundary_summary": "第二轮边界复核仍保留同一入口、主界面和出口。",
        }
    )
    assert player.append_gameplay_candidate(successor) == successor
    assert player.get_gameplay_candidate(ENVIRONMENT_ID, candidate.id) == successor
    assert player.list_gameplay_candidates(ENVIRONMENT_ID) == [successor]

    with pytest.raises(ValueError, match="task is missing"):
        player.append_gameplay_candidate(
            candidate.model_copy(
                update={
                    "id": "gameplay.fixture.missing-task",
                    "triggering_task_ids": ["task.missing"],
                }
            )
        )


def test_speech_draft_send_boundary_requires_policy_task_and_terminal_evidence(tmp_path):
    observatory, player, evidence, task, _before, _main, _edge = initialized_store(tmp_path)
    policy = player.append_account_policy(
        AccountActionPolicyV1(
            id="policy.gameplay-speech.fixture",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[evidence],
            ai_identity_label="设施测试 AI 玩家",
        )
    )
    intent = player.append_speech_intent(
        SpeechIntentV1(
            id="speech-intent.fixture",
            environment_id=ENVIRONMENT_ID,
            evidence_refs=[evidence],
            policy_id=policy.id,
            triggering_task_id=task.id,
            ai_identity_label=policy.ai_identity_label,
            channel="游戏内聊天",
            recipients=["fixture-recipient"],
            purpose="回答教学要求且明确 AI 身份",
            message_text="我是负责这个账号的 AI 玩家。",
            status="authorized",
            policy_disposition="autonomous",
        )
    )
    action_run = RunResult(
        id="run.speech.fixture",
        adapter="fixture",
        target_id="device.fixture",
        task_id=task.id,
        status="passed",
        artifact_ids=evidence.artifact_ids,
    )
    evidence_run = EvidenceRun(
        id="evidence.speech.fixture",
        target_id="device.fixture",
        adapter="fixture",
        status="passed",
        game_id="fixture-game",
        build_scope_id="build.fixture",
        scope_id=ENVIRONMENT_ID,
        viewport_width=1280,
        viewport_height=720,
        orientation="landscape",
        environment={"environment_id": ENVIRONMENT_ID},
        step_ids=["evidence-step.speech.fixture"],
        artifact_ids=evidence.artifact_ids,
    )
    evidence_step = EvidenceStep(
        id="evidence-step.speech.fixture",
        evidence_run_id=evidence_run.id,
        step_index=1,
        status="passed",
        action=NormalizedAction(type="key", key="ENTER"),
        artifact_ids=evidence.artifact_ids,
        viewport_width=1280,
        viewport_height=720,
    )
    observatory.save_run(action_run)
    observatory.save_evidence_run(evidence_run)
    observatory.save_evidence_step(evidence_step)
    send_evidence = EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=evidence.artifact_ids,
        evidence_run_ids=[evidence_run.id],
        evidence_step_ids=[evidence_step.id],
        trace_run_ids=[action_run.id],
    )
    event = SpeechEventV1(
        id="speech-event.fixture.sent",
        environment_id=ENVIRONMENT_ID,
        evidence_refs=[send_evidence],
        speech_intent_id=intent.id,
        speech_intent_version=intent.version,
        status="sent",
        evidence_step_id=evidence_step.id,
        action_run_id=action_run.id,
        system_response="消息已进入游戏内聊天记录。",
    )
    assert player.append_speech_event(event) == event
    assert player.list_speech_events(
        ENVIRONMENT_ID,
        speech_intent_id=intent.id,
    ) == [event]

    draft = player.append_speech_intent(
        intent.model_copy(
            update={
                "id": "speech-intent.fixture.draft",
                "status": "draft",
            }
        )
    )
    with pytest.raises(ValueError, match="authorized speech intent"):
        player.append_speech_event(
            event.model_copy(
                update={
                    "id": "speech-event.fixture.illegal-send",
                    "speech_intent_id": draft.id,
                }
            )
        )