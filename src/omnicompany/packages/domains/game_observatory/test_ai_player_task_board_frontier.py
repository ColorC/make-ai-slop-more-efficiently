from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    SemanticStateV1,
    TransitionEdgeV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.frontier import (
    FrontierGenerator,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.ai_player.task_board import (
    TaskBoard,
    TaskBoardPolicy,
    named_blockers,
)
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    NormalizedAction,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


ENVIRONMENT_ID = "environment.task-board.fixture"
NOW = datetime(2026, 7, 16, 12, tzinfo=timezone.utc)


def _reference() -> EvidenceReferenceV1:
    return EvidenceReferenceV1(
        environment_id=ENVIRONMENT_ID,
        artifact_ids=["artifact.task-board.fixture"],
    )


def _task(
    task_id: str,
    *,
    status: str = "queued",
    dependencies: list[str] | None = None,
    value: float = 1,
    risk: float = 0,
    cooldown_until: str | None = None,
    title: str | None = None,
    reason: str | None = None,
    attempt_count: int = 0,
    max_attempts: int = 2,
) -> FrontierTaskV1:
    updates: dict[str, object] = {}
    if status == "blocked":
        updates = {
            "blocked_reason": "当前入口尚未解锁",
            "reactivation_condition": "主城达到下一等级后重查",
        }
    return FrontierTaskV1(
        id=task_id,
        environment_id=ENVIRONMENT_ID,
        title=title or task_id,
        source="coverage_gap",
        reason=reason or f"闭合 {task_id} 的覆盖缺口",
        status=status,
        dependency_task_ids=dependencies or [],
        value_score=value,
        novelty_score=2,
        expected_coverage_gain=3,
        risk_score=risk,
        action_budget=5,
        time_budget_seconds=120,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        cooldown_until=cooldown_until,
        evidence_refs=[_reference()],
        **updates,
    )


def _store(tmp_path: Path) -> AIPlayerStore:
    observatory = ObservatoryStore(tmp_path / "observatory")
    body = b"task-board-fixture"
    artifact_path = observatory.artifact_root / "task-board-fixture.bin"
    artifact_path.write_bytes(body)
    observatory.save_artifact(
        ArtifactRef(
            id="artifact.task-board.fixture",
            kind="screenshot",
            path=str(artifact_path),
            sha256=hashlib.sha256(body).hexdigest(),
            captured_at="2026-07-16T12:00:00Z",
            metadata={"environment_id": ENVIRONMENT_ID},
        )
    )
    player = AIPlayerStore(observatory)
    player.put_environment(
        EnvironmentScopeV1(
            id=ENVIRONMENT_ID,
            game_id="fixture-game",
            build_scope_id="fixture-build",
            account_scope_id="fixture-account",
            channel="fixture",
            device_scope_id="fixture-device",
            viewport_width=1080,
            viewport_height=1920,
            identity_hash="fixture-identity",
            evidence_refs=[_reference()],
        )
    )
    return player


def test_task_board_prioritizes_explainably_and_names_every_blocker():
    tasks = [
        _task("done", status="completed"),
        _task("needs-done", dependencies=["done"], value=9),
        _task("blocked", status="blocked"),
        _task(
            "cooling",
            status="cooldown",
            cooldown_until=(NOW + timedelta(hours=1)).isoformat(),
        ),
        _task("risky", value=100, risk=9),
    ]
    decision = TaskBoard(TaskBoardPolicy(max_risk_score=5)).select(tasks, now=NOW)

    assert decision.selected_task_id == "needs-done"
    assert decision.idle_allowed is False
    assert "价值 9.00" in decision.reason
    assert decision.disposition("blocked").disposition == "blocked"
    assert "重新激活条件" in decision.disposition("blocked").reason
    assert decision.disposition("cooling").disposition == "cooldown_active"
    assert decision.disposition("risky").disposition == "risk_exceeds_limit"
    assert len(list(named_blockers(decision))) == 3


def test_task_board_deduplicates_and_reactivates_expired_cooldown(tmp_path: Path):
    store = _store(tmp_path)
    old = _task(
        "old",
        status="cooldown",
        cooldown_until=(NOW - timedelta(seconds=1)).isoformat(),
        value=4,
        title="复核同一入口",
        reason="补齐同一个入口",
    )
    duplicate = _task(
        "duplicate",
        value=2,
        title="复核同一入口",
        reason="补齐同一个入口",
    )
    store.enqueue_task(old.model_copy(update={"status": "queued", "cooldown_until": None}))
    stored = store.compare_and_swap_task_status(
        ENVIRONMENT_ID,
        old.id,
        "queued",
        "cooldown",
        expected_version=1,
        updates={"cooldown_until": old.cooldown_until},
    )
    assert stored is not None
    store.enqueue_task(duplicate)

    decision = TaskBoard().select(store.list_tasks(ENVIRONMENT_ID), now=NOW)
    assert decision.selected_task_id == "old"
    assert decision.disposition("duplicate").duplicate_of == "old"
    activated = TaskBoard().activate_selected(store, ENVIRONMENT_ID, decision)
    assert activated is not None
    assert activated.status == "active"
    assert activated.cooldown_until is None


def test_public_task_queue_counterexamples_match_the_frozen_oracle():
    path = (
        Path(__file__).resolve().parents[3]
        / "data/domains/game_observatory/benchmarks/ai_player/fixtures/"
        "public_counterexamples.v1.json"
    )
    scenarios = json.loads(path.read_text(encoding="utf-8"))["task_queue_scenarios"]
    assert len(scenarios) == 100
    for scenario in scenarios:
        tasks = []
        for item in scenario["tasks"]:
            status = item["status"]
            tasks.append(
                _task(
                    item["id"],
                    status=status,
                    cooldown_until=(
                        (NOW + timedelta(days=1)).isoformat()
                        if status == "cooldown"
                        else None
                    ),
                    value=10 if item["id"].endswith(".safe") else 1,
                )
            )
        decision = TaskBoard().select(tasks, now=NOW)
        oracle = scenario["oracle"]
        assert decision.selected_task_id == oracle["expected_next_task_id"], scenario["id"]
        assert decision.idle_allowed is oracle["idle_allowed"], scenario["id"]
        if oracle["all_remaining_tasks_need_named_reason"]:
            nonterminal = [
                item
                for item in decision.dispositions
                if item.disposition not in {"terminal", "eligible", "duplicate"}
            ]
            assert nonterminal and all(item.reason for item in nonterminal), scenario["id"]


def test_frontier_audit_generates_evidence_bound_tasks_idempotently(tmp_path: Path):
    store = _store(tmp_path)
    reference = _reference()
    candidate = SemanticStateV1(
        id="state.candidate",
        environment_id=ENVIRONMENT_ID,
        title="候选建设界面",
        description="仍需确认入口、出口和关键交互。",
        semantic_fingerprint="candidate-build-screen",
        observation_feature_hashes=["1" * 64],
        evidence_refs=[reference],
    )
    accepted = SemanticStateV1(
        id="state.accepted",
        environment_id=ENVIRONMENT_ID,
        title="已接受世界界面",
        description="已确认的世界主界面。",
        semantic_fingerprint="accepted-world-screen",
        observation_feature_hashes=["2" * 64],
        status="accepted",
        evidence_refs=[reference],
    )
    store.put_semantic_state(candidate)
    store.put_semantic_state(accepted)
    store.put_transition_edge(
        TransitionEdgeV1(
            id="edge.deferred",
            environment_id=ENVIRONMENT_ID,
            from_state_id=accepted.id,
            to_state_id=candidate.id,
            action=NormalizedAction(type="tap", x=100, y=200),
            expected_change="进入建设界面",
            observed_change="画面变化但终态尚未确认",
            outcome="deferred",
            evidence_refs=[reference],
        )
    )

    generator = FrontierGenerator(store)
    first = generator.generate(ENVIRONMENT_ID)
    second = generator.generate(ENVIRONMENT_ID)
    assert first.signal_count == 2
    assert len(first.generated_task_ids) == 2
    assert second.generated_task_ids == []
    assert sorted(second.existing_task_ids) == sorted(first.generated_task_ids)
    tasks = store.list_tasks(ENVIRONMENT_ID)
    assert {task.source for task in tasks} == {"coverage_gap", "missing_transition"}
    assert all(task.evidence_refs == [reference] for task in tasks)

    reopened = AIPlayerStore(ObservatoryStore(store.observatory_store.root))
    assert reopened.list_tasks(ENVIRONMENT_ID) == tasks