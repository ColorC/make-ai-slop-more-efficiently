from __future__ import annotations

import hashlib
from pathlib import Path

from omnicompany.packages.domains.game_observatory.exploration import (
    ExplorationDecision,
    ExplorationPolicy,
    ExplorationRunner,
    ScriptedExplorationPlanner,
)
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    BenchmarkTask,
    NormalizedAction,
    ObjectiveCheck,
    ObservationBundle,
    TargetInfo,
)
from omnicompany.packages.domains.game_observatory.store import ObservatoryStore


class FakeAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = 0
        self.actions: list[NormalizedAction] = []

    def connect(self, target: str):  # pragma: no cover - protocol completeness
        return target

    def observe(self) -> ObservationBundle:
        raw = (
            b"\x89PNG\r\
\x1a\
"
            + (b"before" if self.index == 0 else b"after")
        )
        path = self.root / f"frame-{self.index}.png"
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        return ObservationBundle(
            target_id="fixture://exploration",
            frame=ArtifactRef(
                id=f"art.frame.{self.index}",
                kind="screenshot",
                path=str(path),
                sha256=digest,
                media_type="image/png",
            ),
        )

    def act(self, action: NormalizedAction) -> dict:
        self.actions.append(action)
        self.index += 1
        return {"ok": True, "step": self.index}

    def reset(self, snapshot=None):  # pragma: no cover - protocol completeness
        return {}

    def checkpoint(self):  # pragma: no cover - protocol completeness
        return "fixture"

    def restore(self, snapshot):  # pragma: no cover - protocol completeness
        return {}

    def evaluate(self, task):  # pragma: no cover - protocol completeness
        return task


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        id="task.exploration.fixture",
        title="Reach the second fixture state",
        start_state="before",
        goal="after",
        allowed_actions=["wait"],
        reset_method="fixture reset",
        checks=[ObjectiveCheck(id="state_after", description="state changed", expected=True)],
    )


def test_exploration_runner_preserves_decision_action_and_objective_trace(tmp_path):
    store = ObservatoryStore(tmp_path / "store")
    adapter = FakeAdapter(tmp_path)
    planner = ScriptedExplorationPlanner(
        [
            ExplorationDecision(
                surface_summary="fixture before",
                action=NormalizedAction(type="wait", seconds=0),
                rationale="Advance the deterministic fixture.",
                expected_change="The after frame appears.",
                provider="scripted-exploration",
            )
        ]
    )

    def checker(_task, _observation, history):
        actual = bool(history) or adapter.index > 0
        return [
            ObjectiveCheck(
                id="state_after",
                description="state changed",
                expected=True,
                actual=actual,
                passed=actual,
            )
        ]

    outcome = ExplorationRunner(store).run(
        adapter=adapter,
        target=TargetInfo(
            id="fixture://exploration",
            kind="fixture",
            label="fixture",
            status="online",
            capabilities=["pixel"],
        ),
        task=_task(),
        planner=planner,
        policy=ExplorationPolicy(allowed_action_types=["wait"], max_steps=2),
        checker=checker,
    )

    assert outcome.run.status == "passed"
    assert outcome.stop_reason == "objective_met"
    assert outcome.steps[0].decision.action.type == "wait"
    assert outcome.steps[0].novel_state is True
    assert Path(outcome.trace_artifact.path).is_file()
    trace = Path(outcome.trace_artifact.path).read_text(encoding="utf-8")
    assert "planner_decision" in trace
    assert "post_action_observation" in trace


def test_exploration_runner_fails_closed_and_preserves_scene(tmp_path):
    store = ObservatoryStore(tmp_path / "store")
    adapter = FakeAdapter(tmp_path)
    planner = ScriptedExplorationPlanner(
        [
            ExplorationDecision(
                surface_summary="purchase checkout",
                action=NormalizedAction(type="tap", x=10, y=10),
                rationale="Unsafe fixture action.",
                expected_change="Must not execute.",
                provider="scripted-exploration",
            )
        ]
    )
    outcome = ExplorationRunner(store).run(
        adapter=adapter,
        target=TargetInfo(
            id="fixture://exploration",
            kind="fixture",
            label="fixture",
            status="online",
            capabilities=["pixel"],
        ),
        task=_task().model_copy(update={"allowed_actions": ["tap"]}),
        planner=planner,
        policy=ExplorationPolicy(allowed_action_types=["tap"], max_steps=1),
        checker=lambda *_: [],
    )

    assert outcome.run.status == "failed"
    assert outcome.stop_reason == "runtime_error"
    assert not adapter.actions
    assert "prohibited surface" in (outcome.run.error or "")
    assert Path(outcome.trace_artifact.path).is_file()