from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicompany.packages.domains.game_observatory.ai_player.known_route_program import (
    KnownRouteProgram,
)


def _skill(
    skill_id: str,
    *,
    title: str,
    source: str,
    terminal: str,
    actions: int,
):
    steps = [
        SimpleNamespace(kind="action", expected_state_id=None)
        for _ in range(actions)
    ]
    steps.append(SimpleNamespace(kind="assert", expected_state_id=terminal))
    return SimpleNamespace(
        id=skill_id,
        skill_id=skill_id.removesuffix(".version.1"),
        title=title,
        applicability=f"从 {source} 进入 {terminal}",
        preconditions=["当前界面已识别"],
        procedure_steps=[title],
        success_checks=[f"显示 {terminal}"],
        status="candidate",
        executor_kind="normalized_actions",
        safety_level="reversible",
        steps=steps,
        applicability_scope=SimpleNamespace(required_state_ids=[source]),
    )


def _successful_run(skill_id: str, latency_ms: float):
    return SimpleNamespace(
        skill_version_id=skill_id,
        outcome="success",
        objective_success=True,
        validation_passed=True,
        false_success=False,
        safety_violation_count=0,
        decision_latency_ms=latency_ms,
        baseline_decision_latency_ms=60000.0,
        baseline_model_input_tokens=20000,
    )


class _Store:
    def __init__(self):
        self.skills = [
            _skill(
                "skill.city-to-formation.version.1",
                title="主城 → 编队换将",
                source="state.city.canonical",
                terminal="state.formation.replace",
                actions=3,
            )
        ]
        self.runs = {
            self.skills[0].id: [_successful_run(self.skills[0].id, 12000.0)]
        }
        self.memories = []

    def list_skill_versions(self, _environment_id):
        return self.skills

    def list_skill_runs(self, _environment_id, *, skill_version_id):
        return self.runs.get(skill_version_id, [])

    def list_memories(self, _environment_id):
        return self.memories

    def get_semantic_state(self, _environment_id, _state_id):
        return None


def test_known_route_requires_learned_entry_then_reuses_alias_without_model() -> None:
    store = _Store()
    program = KnownRouteProgram(store)

    with pytest.raises(LookupError):
        program.plan(
            "environment.fixture",
            "state.city.current",
            "编队换将",
        )
    assert program.goal_source_state_ids(
        "environment.fixture",
        "编队换将",
    ) == ("state.city.canonical",)

    store.memories.append(
        SimpleNamespace(
            status="active",
            kind="procedural",
            payload={
                "schema": "game-observatory.ai-player.known-skill-entry-alias.v1",
                "skill_version_id": store.skills[0].id,
                "observed_state_id": "state.city.current",
                "required_state_id": "state.city.canonical",
            },
        )
    )
    route = program.plan(
        "environment.fixture",
        "state.city.current",
        "编队换将",
    )

    assert route.selected_entry_state_id == "state.city.canonical"
    assert route.skill_version_ids == (store.skills[0].id,)
    assert route.goal_state_id == "state.formation.replace"
    assert route.learned_only is True