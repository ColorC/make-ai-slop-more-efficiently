from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .adapters import AdapterError, GameAdapter
from .models import (
    ArtifactRef,
    BenchmarkTask,
    NormalizedAction,
    ObjectiveCheck,
    ObservationBundle,
    RunResult,
    TargetInfo,
    TraceEvent,
    utc_now,
)
from .store import ObservatoryStore
from .subprocess_policy import headless_process_kwargs


class ExplorationDecision(BaseModel):
    surface_summary: str
    safe_to_act: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    action: NormalizedAction | None = None
    rationale: str
    expected_change: str
    provider: str
    raw: dict[str, Any] = Field(default_factory=dict)


class ExplorationPolicy(BaseModel):
    allowed_action_types: list[str]
    allowed_packages: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=3, ge=1, le=50)
    max_seconds: float = Field(default=180.0, gt=0, le=3600)
    max_state_visits: int = Field(default=2, ge=1, le=10)
    allowed_tap_regions: list[tuple[float, float, float, float]] = Field(default_factory=list)


class ExplorationStep(BaseModel):
    index: int
    before_artifact_id: str
    before_fingerprint: str
    decision: ExplorationDecision
    action_result: dict[str, Any] = Field(default_factory=dict)
    after_artifact_id: str | None = None
    after_fingerprint: str | None = None
    novel_state: bool | None = None
    checks: list[ObjectiveCheck] = Field(default_factory=list)


class ExplorationOutcome(BaseModel):
    planner: str
    run: RunResult
    steps: list[ExplorationStep]
    state_count: int
    edge_count: int
    stop_reason: str
    trace_artifact: ArtifactRef


class ExplorationPlanner(Protocol):
    name: str

    def plan(
        self,
        task: BenchmarkTask,
        observation: ObservationBundle,
        history: list[ExplorationStep],
        policy: ExplorationPolicy,
    ) -> ExplorationDecision: ...


class ScriptedExplorationPlanner:
    """Deterministic planner for fixtures and stable replay comparisons."""

    name = "scripted-exploration"

    def __init__(
        self,
        decisions: list[ExplorationDecision],
        *,
        name: str = "scripted-exploration",
    ) -> None:
        self.decisions = list(decisions)
        self.name = name

    def plan(
        self,
        _task: BenchmarkTask,
        _observation: ObservationBundle,
        history: list[ExplorationStep],
        _policy: ExplorationPolicy,
    ) -> ExplorationDecision:
        if len(history) >= len(self.decisions):
            return ExplorationDecision(
                surface_summary="script exhausted",
                safe_to_act=False,
                action=None,
                rationale="No scripted action remains.",
                expected_change="Stop without mutation.",
                provider=self.name,
            )
        return self.decisions[len(history)]


class CodexCliVisionPlanner:
    """Replaceable screenshot planner using the already-authenticated Codex CLI."""

    name = "codex-cli-vision"

    def __init__(
        self,
        work_root: Path,
        *,
        executable: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 240,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.work_root = work_root.resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.executable = executable or shutil.which("codex") or "codex"
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        self.schema_path = Path(__file__).resolve().parent / "schemas" / "exploration-decision.schema.json"

    @staticmethod
    def _history_summary(history: list[ExplorationStep]) -> list[dict[str, Any]]:
        return [
            {
                "index": item.index,
                "surface": item.decision.surface_summary,
                "action": item.decision.action.model_dump(mode="json") if item.decision.action else None,
                "before": item.before_fingerprint,
                "after": item.after_fingerprint,
                "novel": item.novel_state,
            }
            for item in history[-6:]
        ]

    def plan(
        self,
        task: BenchmarkTask,
        observation: ObservationBundle,
        history: list[ExplorationStep],
        policy: ExplorationPolicy,
    ) -> ExplorationDecision:
        frame = Path(observation.frame.path)
        if not frame.is_file():
            raise AdapterError(f"planner frame is missing: {frame}")
        request_id = uuid.uuid4().hex
        output_path = self.work_root / f"decision-{request_id}.json"
        prompt = (
            "Do not use tools. You are the vision planner inside a safety-gated game research runtime. "
            "Inspect the attached current frame and return one decision matching the supplied JSON schema. "
            "The task goal is authoritative: do not substitute a different exploration objective. Only choose "
            "an action that directly advances task.goal. Never choose payment, purchase, chat, account binding, "
            "consent, deletion, competitive or social actions. Set risk_flags only for risks actually visible "
            "or required by the proposed action; do not list absent risks. If any risk exists, set safe_to_act=false "
            "and action_type=stop. If the goal is already met or no allowed action is justified, use action_type=stop. "
            "Coordinates are pixels in the attached image. TASK_JSON="
            + json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "allowed_action_types": policy.allowed_action_types,
                    "allowed_packages": policy.allowed_packages,
                    "history": self._history_summary(history),
                },
                ensure_ascii=False,
            )
        )
        command = [
            self.executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--image",
            str(frame),
            "--output-schema",
            str(self.schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append(prompt)
        completed = self.runner(
            command,
            cwd=self.work_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
            **headless_process_kwargs(),
        )
        if completed.returncode != 0 or not output_path.is_file():
            detail = (completed.stderr or completed.stdout or "Codex CLI produced no output").strip()
            raise AdapterError(f"Codex vision planner failed: {detail[-1200:]}")
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"Codex vision planner returned invalid JSON: {exc}") from exc
        action_type = str(payload["action_type"])
        action = None
        if action_type != "stop":
            action = NormalizedAction(
                type=action_type,
                x=payload.get("x"),
                y=payload.get("y"),
                x2=payload.get("x2"),
                y2=payload.get("y2"),
                duration_ms=int(payload.get("duration_ms") or 250),
                seconds=float(payload.get("seconds") or 0.5),
                text=payload.get("text"),
                keycode=payload.get("keycode"),
                package=payload.get("package"),
                button=payload.get("button"),
                pressed=payload.get("pressed"),
            )
        return ExplorationDecision(
            surface_summary=str(payload["surface_summary"]),
            safe_to_act=bool(payload["safe_to_act"]),
            risk_flags=[str(item) for item in payload["risk_flags"]],
            action=action,
            rationale=str(payload["rationale"]),
            expected_change=str(payload["expected_change"]),
            provider=self.name,
            raw={
                "decision": payload,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            },
        )


ObjectiveChecker = Callable[
    [BenchmarkTask, ObservationBundle, list[ExplorationStep]],
    list[ObjectiveCheck],
]


class ExplorationRunner:
    """Observation → planning → safety gate → action → evidence loop."""

    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    @staticmethod
    def _fingerprint(observation: ObservationBundle) -> str:
        parts = [observation.frame.sha256]
        if observation.ui_tree:
            parts.append(observation.ui_tree.sha256)
        if observation.runtime_state:
            parts.append(observation.runtime_state.sha256)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    @staticmethod
    def _observation_artifact_ids(observation: ObservationBundle) -> list[str]:
        return [
            observation.frame.id,
            *([observation.ui_tree.id] if observation.ui_tree else []),
            *([observation.runtime_state.id] if observation.runtime_state else []),
        ]

    @staticmethod
    def _frame_size(observation: ObservationBundle) -> tuple[int, int]:
        try:
            import cv2

            image = cv2.imread(str(observation.frame.path))
            if image is not None:
                height, width = image.shape[:2]
                return int(width), int(height)
        except ImportError:
            pass
        return 0, 0

    @classmethod
    def _assert_safe(
        cls,
        task: BenchmarkTask,
        observation: ObservationBundle,
        decision: ExplorationDecision,
        policy: ExplorationPolicy,
    ) -> None:
        action = decision.action
        if action is None:
            return
        if action.type not in policy.allowed_action_types or action.type not in task.allowed_actions:
            raise AdapterError(f"planner action is outside the allowlist: {action.type}")
        if not decision.safe_to_act or decision.risk_flags:
            if action.type not in {"wait", "back", "home"}:
                raise AdapterError(
                    "planner proposed a mutation without an explicit safe decision"
                )
        if action.type in {"tap", "swipe"}:
            width, height = cls._frame_size(observation)
            points = [(action.x, action.y)]
            if action.type == "swipe":
                points.append((action.x2, action.y2))
            if width <= 0 or height <= 0:
                raise AdapterError("cannot validate planner coordinates without image dimensions")
            if any(x is None or y is None or not (0 <= x < width and 0 <= y < height) for x, y in points):
                raise AdapterError("planner coordinates are outside the current frame")
            if policy.allowed_tap_regions and any(
                not any(
                    left <= x / width < right and top <= y / height < bottom
                    for left, top, right, bottom in policy.allowed_tap_regions
                )
                for x, y in points
            ):
                raise AdapterError("planner coordinates are outside the normalized task-safe regions")
        if action.type == "launch" and action.package not in policy.allowed_packages:
            raise AdapterError(f"planner package is outside the allowlist: {action.package}")

    def run(
        self,
        *,
        adapter: GameAdapter,
        target: TargetInfo,
        task: BenchmarkTask,
        planner: ExplorationPlanner,
        policy: ExplorationPolicy,
        checker: ObjectiveChecker,
    ) -> ExplorationOutcome:
        run_id = f"run.exploration.{uuid.uuid4().hex}"
        started_at = utc_now()
        started = time.monotonic()
        trace: list[TraceEvent] = []
        steps: list[ExplorationStep] = []
        fingerprints: dict[str, int] = {}
        artifact_ids: list[str] = []
        status = "stopped"
        stop_reason = "step_budget_exhausted"
        error: str | None = None
        final_checks: list[ObjectiveCheck] = []
        current: ObservationBundle | None = None

        try:
            current = adapter.observe()
            artifact_ids.extend(self._observation_artifact_ids(current))
            current_fp = self._fingerprint(current)
            fingerprints[current_fp] = 1
            trace.append(
                TraceEvent(
                    seq=1,
                    run_id=run_id,
                    event_type="observation",
                    observation_artifact_ids=self._observation_artifact_ids(current),
                    result={"fingerprint": current_fp, "initial": True},
                )
            )
            final_checks = checker(task, current, steps)
            if final_checks and all(item.passed is True for item in final_checks):
                status = "passed"
                stop_reason = "objective_met_at_start"

            while status != "passed" and len(steps) < policy.max_steps:
                if time.monotonic() - started > policy.max_seconds:
                    stop_reason = "time_budget_exhausted"
                    break
                decision = planner.plan(task, current, steps, policy)
                trace.append(
                    TraceEvent(
                        seq=len(trace) + 1,
                        run_id=run_id,
                        event_type="planner_decision",
                        observation_artifact_ids=self._observation_artifact_ids(current),
                        action=decision.action.model_dump(mode="json") if decision.action else None,
                        result=decision.model_dump(mode="json"),
                    )
                )
                self._assert_safe(task, current, decision, policy)
                if decision.action is None:
                    stop_reason = "planner_stopped"
                    break
                before_fp = self._fingerprint(current)
                action_result = adapter.act(decision.action)
                trace.append(
                    TraceEvent(
                        seq=len(trace) + 1,
                        run_id=run_id,
                        event_type="action_result",
                        observation_artifact_ids=self._observation_artifact_ids(current),
                        action=decision.action.model_dump(mode="json"),
                        result=action_result,
                    )
                )
                after = adapter.observe()
                artifact_ids.extend(self._observation_artifact_ids(after))
                after_fp = self._fingerprint(after)
                visits = fingerprints.get(after_fp, 0) + 1
                fingerprints[after_fp] = visits
                step = ExplorationStep(
                    index=len(steps) + 1,
                    before_artifact_id=current.frame.id,
                    before_fingerprint=before_fp,
                    decision=decision,
                    action_result=action_result,
                    after_artifact_id=after.frame.id,
                    after_fingerprint=after_fp,
                    novel_state=visits == 1,
                )
                steps.append(step)
                checks = checker(task, after, steps)
                step.checks = checks
                trace.append(
                    TraceEvent(
                        seq=len(trace) + 1,
                        run_id=run_id,
                        event_type="post_action_observation",
                        observation_artifact_ids=self._observation_artifact_ids(after),
                        result={
                            "fingerprint": after_fp,
                            "novel_state": step.novel_state,
                            "checks": [item.model_dump(mode="json") for item in checks],
                        },
                    )
                )
                final_checks = checks
                current = after
                if checks and all(item.passed is True for item in checks):
                    status = "passed"
                    stop_reason = "objective_met"
                    break
                if visits > policy.max_state_visits:
                    stop_reason = "repeated_state_limit"
                    break
        except Exception as exc:  # failed scenes and the exact error stay in the trace
            status = "failed"
            stop_reason = "runtime_error"
            error = str(exc)
            trace.append(
                TraceEvent(
                    seq=len(trace) + 1,
                    run_id=run_id,
                    event_type="failure",
                    observation_artifact_ids=(
                        self._observation_artifact_ids(current) if current else []
                    ),
                    result={"error": error, "type": type(exc).__name__},
                )
            )

        raw_trace = "".join(item.model_dump_json() + "\n" for item in trace).encode("utf-8")
        digest = hashlib.sha256(raw_trace).hexdigest()
        trace_artifact = ArtifactRef(
            id=f"art.exploration-trace.{digest[:16]}",
            kind="trace",
            path=str(self.store.artifact_root / f"art.exploration-trace.{digest[:16]}.jsonl"),
            sha256=digest,
            run_id=run_id,
            media_type="application/x-ndjson",
            metadata={
                "planner": planner.name,
                "stop_reason": stop_reason,
                "public": False,
            },
        )
        Path(trace_artifact.path).write_bytes(raw_trace)
        self.store.save_artifact(trace_artifact)
        artifact_ids.append(trace_artifact.id)
        run = RunResult(
            id=run_id,
            adapter=f"exploration:{planner.name}",
            target_id=target.id,
            task_id=task.id,
            status=status,
            started_at=started_at,
            ended_at=utc_now(),
            checks=final_checks,
            artifact_ids=list(dict.fromkeys(artifact_ids)),
            error=error or (None if status == "passed" else stop_reason),
        )
        self.store.save_run(run)
        self.store.append_event(
            run.id,
            "exploration_complete",
            {
                "planner": planner.name,
                "status": status,
                "stop_reason": stop_reason,
                "steps": len(steps),
                "states": len(fingerprints),
                "trace": trace_artifact.id,
            },
        )
        return ExplorationOutcome(
            planner=planner.name,
            run=run,
            steps=steps,
            state_count=len(fingerprints),
            edge_count=len(steps),
            stop_reason=stop_reason,
            trace_artifact=trace_artifact,
        )
