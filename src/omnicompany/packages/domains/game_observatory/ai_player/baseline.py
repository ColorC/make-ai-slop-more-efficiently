"""Deterministic phase-0 replay baseline around the legacy ExplorationRunner."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import re
import struct
import time
import zlib
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..adapters import AdapterError, GameAdapter
from ..exploration import (
    ExplorationDecision,
    ExplorationPlanner,
    ExplorationPolicy,
    ExplorationRunner,
    ScriptedExplorationPlanner,
)
from ..models import (
    ArtifactRef,
    BenchmarkTask,
    NormalizedAction,
    ObjectiveCheck,
    ObservationBundle,
    RunResult,
    TargetInfo,
    utc_now,
)
from ..store import ObservatoryStore
from .contracts import EnvironmentScopeV1, EvidenceReferenceV1
from .store import AIPlayerStore


class _StrictBaselineContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ReplayStateV1(_StrictBaselineContract):
    id: str = Field(min_length=1)
    frame_seed: str = Field(min_length=1)
    ui_tree: dict[str, Any] | None = None
    runtime_state: dict[str, Any] | None = None


class ReplayTransitionV1(_StrictBaselineContract):
    from_state_id: str = Field(min_length=1)
    action: NormalizedAction
    to_state_id: str = Field(min_length=1)
    action_result: dict[str, Any] = Field(default_factory=lambda: {"ok": True})


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _action_key(action: NormalizedAction) -> str:
    return _canonical_json(action.model_dump(mode="json"))


class ExplorationBaselineFixtureV1(_StrictBaselineContract):
    schema_id: Literal["game-observatory.ai-player.exploration-baseline-fixture.v1"] = Field(
        default="game-observatory.ai-player.exploration-baseline-fixture.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    build_scope_id: str = Field(min_length=1)
    account_scope_id: str = Field(min_length=1)
    target_id: str = Field(pattern=r"^fixture://", min_length=11)
    start_state_id: str = Field(min_length=1)
    goal_state_id: str = Field(min_length=1)
    states: list[ReplayStateV1] = Field(min_length=1)
    transitions: list[ReplayTransitionV1] = Field(default_factory=list)
    task: BenchmarkTask
    policy: ExplorationPolicy
    planner_name: str = Field(default="scripted-exploration-baseline", min_length=1)
    planner_decisions: list[ExplorationDecision] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_replay_graph(self) -> "ExplorationBaselineFixtureV1":
        state_ids = [state.id for state in self.states]
        state_id_set = set(state_ids)
        if len(state_ids) != len(state_id_set):
            raise ValueError("replay state ids must be unique")
        if self.start_state_id not in state_id_set:
            raise ValueError("start state does not resolve")
        if self.goal_state_id not in state_id_set:
            raise ValueError("goal state does not resolve")
        if self.task.start_state != self.start_state_id or self.task.goal != self.goal_state_id:
            raise ValueError("benchmark task start and goal must match the replay fixture")

        transition_keys: set[tuple[str, str]] = set()
        allowed_by_task = set(self.task.allowed_actions)
        allowed_by_policy = set(self.policy.allowed_action_types)
        for transition in self.transitions:
            if (
                transition.from_state_id not in state_id_set
                or transition.to_state_id not in state_id_set
            ):
                raise ValueError("replay transition state does not resolve")
            if (
                transition.action.type not in allowed_by_task
                or transition.action.type not in allowed_by_policy
            ):
                raise ValueError("replay transition action is outside the task or policy allowlist")
            key = (transition.from_state_id, _action_key(transition.action))
            if key in transition_keys:
                raise ValueError(
                    "replay transitions must be deterministic for each state-action pair"
                )
            transition_keys.add(key)

        for decision in self.planner_decisions:
            if decision.action is None:
                continue
            if (
                decision.action.type not in allowed_by_task
                or decision.action.type not in allowed_by_policy
            ):
                raise ValueError("planner decision action is outside the task or policy allowlist")
        return self

    def fixture_hash(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True)
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class ExplorationBaselineResultV1(_StrictBaselineContract):
    schema_id: Literal["game-observatory.ai-player.exploration-baseline-result.v1"] = Field(
        default="game-observatory.ai-player.exploration-baseline-result.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    fixture_id: str = Field(min_length=1)
    fixture_hash: str = Field(min_length=64, max_length=64)
    code_hash: str = Field(min_length=64, max_length=64)
    config_hash: str = Field(min_length=64, max_length=64)
    run_id: str = Field(min_length=1)
    run_status: Literal["running", "passed", "failed", "stopped"]
    action_count: int = Field(ge=0)
    action_attempt_count: int = Field(ge=0)
    planner_call_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    raw_fingerprint_count: int = Field(ge=0)
    executed_step_count: int = Field(ge=0)
    unique_observed_transition_count: int = Field(ge=0)
    repeated_state_action_count: int = Field(ge=0)
    no_change_action_count: int = Field(ge=0)
    goal_state_id: str = Field(min_length=1)
    final_state_id: str = Field(min_length=1)
    objective_status: Literal["met", "not_met", "indeterminate"]
    stop_reason: str = Field(min_length=1)
    elapsed_seconds: float = Field(ge=0)
    planner_latency_seconds: float = Field(ge=0)
    planner_latency_samples: list[float] = Field(default_factory=list)
    token_measurement_status: Literal["available", "unavailable"]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    evidence_required_count: int = Field(ge=0)
    evidence_resolved_count: int = Field(ge=0)
    evidence_reference_completeness: float = Field(ge=0, le=1)
    trace_artifact_id: str = Field(min_length=1)
    persistence_status: Literal["not_requested", "verified"] = "not_requested"
    ai_player_store_schema_version: int | None = Field(default=None, ge=1)
    error: str | None = None
    generated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_measurements(self) -> "ExplorationBaselineResultV1":
        token_values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.token_measurement_status == "unavailable" and any(
            value is not None for value in token_values
        ):
            raise ValueError("unavailable token measurements must use null values")
        if self.token_measurement_status == "available" and any(
            value is None for value in token_values
        ):
            raise ValueError("available token measurements require all token values")
        if self.evidence_resolved_count > self.evidence_required_count:
            raise ValueError("resolved evidence cannot exceed required evidence")
        expected_completeness = (
            self.evidence_resolved_count / self.evidence_required_count
            if self.evidence_required_count
            else 0.0
        )
        if abs(self.evidence_reference_completeness - expected_completeness) > 1e-9:
            raise ValueError("evidence completeness must match resolved and required counts")
        if len(self.planner_latency_samples) != self.planner_call_count:
            raise ValueError("planner latency samples must match planner call count")
        if self.persistence_status == "verified" and self.ai_player_store_schema_version is None:
            raise ValueError("verified persistence requires an AI-player store schema version")
        return self


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _deterministic_png(seed: str) -> bytes:
    """Return a valid 2x2 RGB PNG whose pixels are derived from the state seed."""

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    pixel_bytes = (digest * 2)[:12]
    scanlines = b"\x00" + pixel_bytes[:6] + b"\x00" + pixel_bytes[6:12]
    header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines))
        + _png_chunk(b"IEND", b"")
    )


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "state"


class ReplayExplorationAdapter(GameAdapter):
    """A frozen state machine that emits canonical ObservationBundle artifacts."""

    def __init__(self, store: ObservatoryStore, fixture: ExplorationBaselineFixtureV1) -> None:
        self.store = store
        self.fixture = fixture
        self.states = {state.id: state for state in fixture.states}
        self.transitions = {
            (transition.from_state_id, _action_key(transition.action)): transition
            for transition in fixture.transitions
        }
        self.current_state_id = fixture.start_state_id
        self.connected = False
        self.action_attempt_count = 0
        self.action_count = 0
        self.observation_count = 0
        self.observation_artifact_ids: list[str] = []

    def connect(self, target: str) -> TargetInfo:
        if target != self.fixture.target_id:
            raise AdapterError(
                f"replay target mismatch: expected {self.fixture.target_id}, received {target}"
            )
        self.connected = True
        return TargetInfo(
            id=target,
            kind="fixture",
            label=self.fixture.title,
            status="online",
            capabilities=["pixel", "deterministic_replay", "objective_checker"],
            metadata={
                "fixture_id": self.fixture.id,
                "environment_id": self.fixture.environment_id,
            },
        )

    def _save_artifact(
        self,
        *,
        state: ReplayStateV1,
        kind: Literal["screenshot", "ui_tree", "runtime_state"],
        raw: bytes,
        suffix: str,
        media_type: str,
    ) -> ArtifactRef:
        fixture_digest = self.fixture.fixture_hash()[:16]
        artifact_id = f"art.ai-player-baseline.{fixture_digest}.{_safe_name(state.id)}.{suffix}"
        directory = self.store.artifact_root / "ai_player_baseline" / fixture_digest
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_safe_name(state.id)}.{suffix}"
        path.write_bytes(raw)
        artifact = ArtifactRef(
            id=artifact_id,
            kind=kind,
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            media_type=media_type,
            metadata={
                "fixture_id": self.fixture.id,
                "environment_id": self.fixture.environment_id,
                "state_id": state.id,
                "deterministic": True,
                "public": False,
            },
        )
        self.store.save_artifact(artifact)
        return artifact

    def observe(self) -> ObservationBundle:
        if not self.connected:
            raise AdapterError("connect the replay adapter before observing")
        state = self.states[self.current_state_id]
        frame = self._save_artifact(
            state=state,
            kind="screenshot",
            raw=_deterministic_png(state.frame_seed),
            suffix="png",
            media_type="image/png",
        )
        ui_tree = None
        if state.ui_tree is not None:
            ui_tree = self._save_artifact(
                state=state,
                kind="ui_tree",
                raw=_canonical_json(state.ui_tree).encode("utf-8"),
                suffix="ui.json",
                media_type="application/json",
            )
        runtime_state = None
        if state.runtime_state is not None:
            runtime_state = self._save_artifact(
                state=state,
                kind="runtime_state",
                raw=_canonical_json(state.runtime_state).encode("utf-8"),
                suffix="runtime.json",
                media_type="application/json",
            )
        bundle = ObservationBundle(
            target_id=self.fixture.target_id,
            frame=frame,
            ui_tree=ui_tree,
            runtime_state=runtime_state,
            metadata={
                "fixture_id": self.fixture.id,
                "environment_id": self.fixture.environment_id,
                "replay_state_id": state.id,
            },
        )
        self.observation_count += 1
        self.observation_artifact_ids.extend(
            [
                frame.id,
                *([ui_tree.id] if ui_tree else []),
                *([runtime_state.id] if runtime_state else []),
            ]
        )
        return bundle

    def reset(self, snapshot: str | None = None) -> dict[str, Any]:
        state_id = snapshot or self.fixture.start_state_id
        if state_id not in self.states:
            raise AdapterError(f"replay reset state does not resolve: {state_id}")
        self.current_state_id = state_id
        return {"ok": True, "state_id": state_id}

    def checkpoint(self) -> str:
        return self.current_state_id

    def restore(self, snapshot: str) -> dict[str, Any]:
        return self.reset(snapshot)

    def act(self, action: NormalizedAction) -> dict[str, Any]:
        if not self.connected:
            raise AdapterError("connect the replay adapter before acting")
        self.action_attempt_count += 1
        key = (self.current_state_id, _action_key(action))
        transition = self.transitions.get(key)
        if transition is None:
            expected = [
                json.loads(action_key)
                for (state_id, action_key), _transition in self.transitions.items()
                if state_id == self.current_state_id
            ]
            raise AdapterError(
                "replay action mismatch at state "
                f"{self.current_state_id}: expected={_canonical_json(expected)} "
                f"received={_action_key(action)}"
            )
        previous = self.current_state_id
        self.current_state_id = transition.to_state_id
        self.action_count += 1
        return {
            **transition.action_result,
            "fixture_id": self.fixture.id,
            "from_state_id": previous,
            "to_state_id": self.current_state_id,
            "action_index": self.action_count,
        }

    def evaluate(self, task: BenchmarkTask) -> RunResult:
        passed = self.current_state_id == self.fixture.goal_state_id
        return RunResult(
            id=f"run.ai-player-baseline-evaluate.{self.fixture.fixture_hash()[:16]}",
            adapter="ai-player-baseline-replay",
            target_id=self.fixture.target_id,
            task_id=task.id,
            status="passed" if passed else "stopped",
            ended_at=utc_now(),
            checks=[
                ObjectiveCheck(
                    id=check.id,
                    description=check.description,
                    expected=check.expected,
                    actual=passed,
                    passed=passed,
                )
                for check in task.checks
            ],
        )


TokenUsageGetter = Callable[[], dict[str, int] | None]


class MeteredPlanner:
    """Measure planner calls and latency without changing planner decisions."""

    def __init__(
        self,
        delegate: ExplorationPlanner,
        *,
        token_usage_getter: TokenUsageGetter | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.delegate = delegate
        self.name = delegate.name
        self.token_usage_getter = token_usage_getter
        self.clock = clock
        self.call_count = 0
        self.latency_samples: list[float] = []

    def plan(
        self,
        task: BenchmarkTask,
        observation: ObservationBundle,
        history: list[Any],
        policy: ExplorationPolicy,
    ) -> ExplorationDecision:
        started = self.clock()
        try:
            return self.delegate.plan(task, observation, history, policy)
        finally:
            self.call_count += 1
            self.latency_samples.append(max(0.0, self.clock() - started))

    def token_measurement(
        self,
    ) -> tuple[Literal["available", "unavailable"], int | None, int | None, int | None]:
        if self.token_usage_getter is None:
            return "unavailable", None, None, None
        usage = self.token_usage_getter()
        if usage is None:
            return "unavailable", None, None, None
        required = ("input_tokens", "output_tokens", "total_tokens")
        if any(
            key not in usage or not isinstance(usage[key], int) or usage[key] < 0
            for key in required
        ):
            return "unavailable", None, None, None
        return (
            "available",
            usage["input_tokens"],
            usage["output_tokens"],
            usage["total_tokens"],
        )


def _hash_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted((item.resolve() for item in paths), key=str):
        digest.update(str(path.name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def baseline_code_paths() -> list[Path]:
    """Return every implementation input represented by a committed baseline hash."""

    ai_player_root = Path(__file__).resolve().parent
    module_root = ai_player_root.parent
    return [
        Path(__file__),
        ai_player_root / "contracts.py",
        ai_player_root / "store.py",
        module_root / "adapters.py",
        module_root / "exploration.py",
        module_root / "models.py",
        module_root / "store.py",
        *sorted((ai_player_root / "migrations").glob("*.sql")),
    ]


def baseline_code_hash() -> str:
    return _hash_files(baseline_code_paths())


def _artifact_resolves(store: ObservatoryStore, artifact_id: str) -> bool:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        return False
    path = Path(artifact.path)
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256


class ExplorationBaselineRunner:
    """Run a frozen fixture through the unchanged ExplorationRunner."""

    def __init__(
        self,
        store: ObservatoryStore,
        *,
        runner_factory: Callable[[ObservatoryStore], ExplorationRunner] = ExplorationRunner,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.store = store
        self.runner_factory = runner_factory
        self.clock = clock

    @staticmethod
    def _objective_status(
        checks: list[ObjectiveCheck],
    ) -> Literal["met", "not_met", "indeterminate"]:
        if not checks or any(check.passed is None for check in checks):
            return "indeterminate"
        return "met" if all(check.passed is True for check in checks) else "not_met"

    def run(
        self,
        fixture: ExplorationBaselineFixtureV1,
        *,
        planner: ExplorationPlanner | None = None,
        token_usage_getter: TokenUsageGetter | None = None,
    ) -> ExplorationBaselineResultV1:
        replay = ReplayExplorationAdapter(self.store, fixture)
        target = replay.connect(fixture.target_id)
        delegate = planner or ScriptedExplorationPlanner(
            fixture.planner_decisions,
            name=fixture.planner_name,
        )
        metered = MeteredPlanner(
            delegate,
            token_usage_getter=token_usage_getter,
            clock=self.clock,
        )

        def checker(
            task: BenchmarkTask,
            _observation: ObservationBundle,
            _history: list[Any],
        ) -> list[ObjectiveCheck]:
            passed = replay.current_state_id == fixture.goal_state_id
            return [
                ObjectiveCheck(
                    id=check.id,
                    description=check.description,
                    expected=check.expected,
                    actual=passed,
                    passed=passed,
                )
                for check in task.checks
            ]

        started = self.clock()
        outcome = self.runner_factory(self.store).run(
            adapter=replay,
            target=target,
            task=fixture.task,
            planner=metered,
            policy=fixture.policy,
            checker=checker,
        )
        elapsed = max(0.0, self.clock() - started)

        seen_state_actions: set[tuple[str, str]] = set()
        observed_transitions: set[tuple[str, str, str]] = set()
        repeated_state_action_count = 0
        no_change_action_count = 0
        for step in outcome.steps:
            action = step.decision.action
            if action is not None:
                action_key = _action_key(action)
                state_action = (step.before_fingerprint, action_key)
                if state_action in seen_state_actions:
                    repeated_state_action_count += 1
                seen_state_actions.add(state_action)
            else:
                action_key = "<no-action>"
            observed_transitions.add((step.before_fingerprint, action_key, step.after_fingerprint))
            if step.after_fingerprint == step.before_fingerprint:
                no_change_action_count += 1

        required_evidence_ids = [
            *replay.observation_artifact_ids,
            outcome.trace_artifact.id,
        ]
        resolved_evidence_count = sum(
            1
            for artifact_id in required_evidence_ids
            if _artifact_resolves(self.store, artifact_id)
        )
        evidence_completeness = (
            resolved_evidence_count / len(required_evidence_ids) if required_evidence_ids else 0.0
        )
        token_status, input_tokens, output_tokens, total_tokens = metered.token_measurement()

        config_payload = {
            "task": fixture.task.model_dump(mode="json"),
            "policy": fixture.policy.model_dump(mode="json"),
            "planner": metered.name,
        }
        config_hash = hashlib.sha256(_canonical_json(config_payload).encode("utf-8")).hexdigest()
        code_hash = baseline_code_hash()
        return ExplorationBaselineResultV1(
            id=f"baseline-result.{outcome.run.id}",
            fixture_id=fixture.id,
            fixture_hash=fixture.fixture_hash(),
            code_hash=code_hash,
            config_hash=config_hash,
            run_id=outcome.run.id,
            run_status=outcome.run.status,
            action_count=replay.action_count,
            action_attempt_count=replay.action_attempt_count,
            planner_call_count=metered.call_count,
            observation_count=replay.observation_count,
            raw_fingerprint_count=outcome.state_count,
            executed_step_count=len(outcome.steps),
            unique_observed_transition_count=len(observed_transitions),
            repeated_state_action_count=repeated_state_action_count,
            no_change_action_count=no_change_action_count,
            goal_state_id=fixture.goal_state_id,
            final_state_id=replay.current_state_id,
            objective_status=self._objective_status(outcome.run.checks),
            stop_reason=outcome.stop_reason,
            elapsed_seconds=elapsed,
            planner_latency_seconds=sum(metered.latency_samples),
            planner_latency_samples=metered.latency_samples,
            token_measurement_status=token_status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            evidence_required_count=len(required_evidence_ids),
            evidence_resolved_count=resolved_evidence_count,
            evidence_reference_completeness=evidence_completeness,
            trace_artifact_id=outcome.trace_artifact.id,
            error=outcome.run.error,
        )


def run_fixture_file(
    fixture_path: Path,
    *,
    store_root: Path,
    output_path: Path,
) -> ExplorationBaselineResultV1:
    fixture = ExplorationBaselineFixtureV1.model_validate_json(
        fixture_path.read_text(encoding="utf-8")
    )
    observatory = ObservatoryStore(store_root)
    result = ExplorationBaselineRunner(observatory).run(fixture)
    trace_artifact = observatory.get_artifact(result.trace_artifact_id)
    if trace_artifact is None:
        raise RuntimeError("baseline trace artifact disappeared before persistence")
    observatory.save_artifact(
        trace_artifact.model_copy(
            update={
                "metadata": {
                    **trace_artifact.metadata,
                    "environment_id": fixture.environment_id,
                }
            }
        )
    )
    trace_reference = EvidenceReferenceV1(
        environment_id=fixture.environment_id,
        artifact_ids=[result.trace_artifact_id],
    )
    identity_payload = {
        "environment_id": fixture.environment_id,
        "game_id": fixture.game_id,
        "build_scope_id": fixture.build_scope_id,
        "account_scope_id": fixture.account_scope_id,
        "channel": str(fixture.metadata.get("channel", "fixture")),
        "target_id": fixture.target_id,
    }
    environment = EnvironmentScopeV1(
        id=fixture.environment_id,
        game_id=fixture.game_id,
        build_scope_id=fixture.build_scope_id,
        account_scope_id=fixture.account_scope_id,
        channel=str(fixture.metadata.get("channel", "fixture")),
        device_scope_id=fixture.target_id,
        locale=str(fixture.metadata.get("locale", "en-US")),
        viewport_width=2,
        viewport_height=2,
        identity_hash=hashlib.sha256(_canonical_json(identity_payload).encode("utf-8")).hexdigest(),
        evidence_refs=[trace_reference],
    )
    player_store = AIPlayerStore(observatory)
    player_store.put_environment(environment)
    result = result.model_copy(
        update={
            "persistence_status": "verified",
            "ai_player_store_schema_version": player_store.schema_version,
        }
    )
    player_store.put_baseline_result(
        fixture.environment_id,
        fixture.id,
        fixture_hash=result.fixture_hash,
        code_hash=result.code_hash,
        config_hash=result.config_hash,
        result=result,
    )
    reopened_store = AIPlayerStore(ObservatoryStore(store_root))
    persisted_payload = reopened_store.get_baseline_result(
        fixture.environment_id,
        fixture.id,
        fixture_hash=result.fixture_hash,
        code_hash=result.code_hash,
        config_hash=result.config_hash,
    )
    if persisted_payload is None:
        raise RuntimeError("baseline result did not survive store reopen")
    persisted = ExplorationBaselineResultV1.model_validate(persisted_payload)
    if persisted != result or reopened_store.get_environment(fixture.environment_id) != environment:
        raise RuntimeError("baseline environment or result changed after store reopen")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one frozen AI-player replay baseline.")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_fixture_file(
        args.fixture,
        store_root=args.store_root,
        output_path=args.output,
    )
    return int(result.run_status != "passed" or result.evidence_reference_completeness != 1.0)


if __name__ == "__main__":
    raise SystemExit(main())
