"""Derive external-Agent action quality from immutable invocation and live evidence.

The external gameplay worker plans several actions inside one native provider turn.  This
module never treats the worker's prose as execution truth.  It binds each sample to the
persisted invocation ledger and to one complete ``ai-player-live-step`` evidence bundle,
then deterministically rebuilds every quality field during storage validation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .account_metric_observation import (
    CanonicalAccountMetricProvider,
    attach_account_metric_derivations,
)
from .contracts import ActionQualitySampleV1, EvidenceReferenceV1
from .external_agent_runtime import (
    ExternalAgentInvocationV1,
    ExternalAgentSessionLedger,
    ExternalAgentTokenUsageV1,
)
from .iteration_monitor import PlayerIterationMonitor

if TYPE_CHECKING:
    from ..models import EvidenceRun, EvidenceStep, RunResult
    from .store import AIPlayerStore


_POINTER_ACTION_TYPES = {
    "tap",
    "swipe",
    "pinch",
    "two_finger_swipe",
    "mouse_move",
    "mouse_button",
}


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("external action-quality timestamps require a UTC offset")
    return parsed


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_command_id(
    invocation: ExternalAgentInvocationV1,
    *,
    task_id: str,
    evidence_step_id: str,
) -> str:
    payload = "\x1f".join(
        (
            invocation.id,
            invocation.event_log_sha256,
            invocation.last_message_sha256,
            task_id,
            evidence_step_id,
        )
    )
    return f"command.external-agent-action.{hashlib.sha256(payload.encode()).hexdigest()[:32]}"


def stable_external_action_quality_sample_id(command_id: str) -> str:
    digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:32]
    return f"action-quality.external-agent-action.{digest}"


def count_confirmed_external_action_effects(
    samples: Iterable[ActionQualitySampleV1 | dict[str, Any]],
) -> int:
    """Count independently measured external actions with a canonical benefit.

    The worker's prose summary is intentionally absent from this projection.  A counted
    action must have a complete terminal EvidenceStep, a measured expectation match and
    an ActionQualitySample whose observable-benefit fields passed the contract validator.
    EvidenceStep ids are unique action boundaries, so duplicate projections cannot inflate
    one provider turn.
    """

    confirmed_step_ids: set[str] = set()
    seen_step_ids: dict[str, str] = {}
    for value in samples:
        sample = (
            value
            if isinstance(value, ActionQualitySampleV1)
            else ActionQualitySampleV1.model_validate(value)
        )
        if not sample.id.startswith("action-quality.external-agent-action."):
            raise ValueError("external action-effect projection received a non-external sample")
        step_id = sample.evidence_step_id
        if step_id is None:
            raise ValueError("external action-effect projection lacks a terminal evidence step")
        previous_sample_id = seen_step_ids.setdefault(step_id, sample.id)
        if previous_sample_id != sample.id:
            raise ValueError("external action-effect projection duplicates one evidence step")
        observable_benefit = any(
            (
                sample.meaningful_change,
                sample.task_progress,
                sample.objective_completed,
                sample.information_gain_units > 0,
                sample.new_state_count > 0,
                sample.new_transition_count > 0,
                sample.new_interface_count > 0,
                sample.new_gameplay_count > 0,
                sample.new_rule_count > 0,
                bool(sample.account_metric_deltas),
            )
        )
        if all(
            (
                sample.execution_disposition == "executed",
                sample.outcome == "confirmed",
                sample.evidence_complete,
                sample.expected_change_measurement_status == "measured",
                sample.expected_change_matched is True,
                observable_benefit,
            )
        ):
            confirmed_step_ids.add(step_id)
    return len(confirmed_step_ids)


def _target_cluster_id(step: "EvidenceStep", before_sha256: str) -> str:
    payload = {
        "action": step.action.model_dump(mode="json"),
        "before_sha256": before_sha256,
        "target_bounds": (
            step.target_bounds.model_dump(mode="json") if step.target_bounds is not None else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"action-cluster.external.{hashlib.sha256(encoded).hexdigest()[:24]}"


def _verified_event_usage(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> ExternalAgentTokenUsageV1 | None:
    """Return provider-reported usage only after verifying the immutable event log."""

    path = (ledger.root / invocation.event_log_path).resolve()
    if not path.is_relative_to(ledger.root) or not path.is_file():
        raise ValueError("external Agent invocation event log is missing or escaped the ledger")
    if _sha256(path) != invocation.event_log_sha256:
        raise ValueError("external Agent invocation event log hash changed")
    message_path = (ledger.root / invocation.last_message_path).resolve()
    if not message_path.is_relative_to(ledger.root) or not message_path.is_file():
        raise ValueError("external Agent invocation last message is missing or escaped the ledger")
    if _sha256(message_path) != invocation.last_message_sha256:
        raise ValueError("external Agent invocation last-message hash changed")

    usage_payloads: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        event = record.get("event") if isinstance(record, dict) else None
        if not isinstance(event, dict):
            continue
        if invocation.provider == "codex-cli":
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage_payloads.append(event["usage"])
        elif event.get("type") == "result" and isinstance(event.get("usage"), dict):
            usage_payloads.append(event["usage"])
        message = event.get("message")
        if invocation.provider == "claude-code-cli" and isinstance(message, dict):
            if isinstance(message.get("usage"), dict):
                usage_payloads.append(message["usage"])
    if not usage_payloads:
        return None

    def integer(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    reported = ExternalAgentTokenUsageV1()
    for raw in usage_payloads:
        reported = ExternalAgentTokenUsageV1(
            input_tokens=max(reported.input_tokens, integer(raw.get("input_tokens"))),
            cached_input_tokens=max(
                reported.cached_input_tokens,
                integer(raw.get("cached_input_tokens"))
                + integer(raw.get("cache_read_input_tokens")),
            ),
            cache_creation_input_tokens=max(
                reported.cache_creation_input_tokens,
                integer(raw.get("cache_creation_input_tokens")),
            ),
            output_tokens=max(reported.output_tokens, integer(raw.get("output_tokens"))),
            reasoning_tokens=max(
                reported.reasoning_tokens,
                integer(raw.get("reasoning_output_tokens")) + integer(raw.get("reasoning_tokens")),
            ),
        )
    if reported != invocation.usage:
        raise ValueError("external Agent invocation usage differs from its hashed event log")
    return reported


def _previous_native_invocation(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> ExternalAgentInvocationV1 | None:
    session = ledger.get_session(invocation.session_id)
    if session is None:
        raise ValueError(f"external Agent session is missing: {invocation.session_id}")
    invocations = ledger.list_invocations(session.id)
    current_index = next(
        (index for index, item in enumerate(invocations) if item.id == invocation.id),
        None,
    )
    if current_index is None:
        raise ValueError(f"external Agent invocation is missing: {invocation.id}")
    if current_index > 0:
        candidate = invocations[current_index - 1]
        if (
            candidate.provider == invocation.provider
            and candidate.external_session_id == invocation.external_session_id
        ):
            return candidate
        return None

    previous_session_id = session.previous_session_id
    visited: set[str] = set()
    while previous_session_id and previous_session_id not in visited:
        visited.add(previous_session_id)
        previous_session = ledger.get_session(previous_session_id)
        if previous_session is None:
            return None
        previous_invocations = ledger.list_invocations(previous_session.id)
        for candidate in reversed(previous_invocations):
            if (
                candidate.provider == invocation.provider
                and candidate.external_session_id == invocation.external_session_id
            ):
                return candidate
        previous_session_id = previous_session.previous_session_id
    return None


def _verified_usage_increment(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> ExternalAgentTokenUsageV1 | None:
    current = _verified_event_usage(ledger, invocation)
    if current is None:
        return None
    if invocation.provider != "codex-cli":
        return current
    previous = _previous_native_invocation(ledger, invocation)
    if previous is None:
        session = ledger.get_session(invocation.session_id)
        if session is None:
            raise ValueError(f"external Agent session is missing: {invocation.session_id}")
        if invocation.operation == "resume" or session.previous_session_id is not None:
            return None
        return current
    previous_usage = _verified_event_usage(ledger, previous)
    if previous_usage is None:
        return None
    current_values = current.model_dump(mode="json")
    previous_values = previous_usage.model_dump(mode="json")
    if any(current_values[key] < previous_values[key] for key in current_values):
        return None
    return ExternalAgentTokenUsageV1.model_validate(
        {key: current_values[key] - previous_values[key] for key in current_values}
    )


def verified_external_usage_increment(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> ExternalAgentTokenUsageV1 | None:
    """Return the hash-verified provider usage attributable to one invocation."""

    return _verified_usage_increment(ledger, invocation)


def _invocation_steps(
    player: "AIPlayerStore",
    invocation: ExternalAgentInvocationV1,
) -> list[tuple["EvidenceRun", "EvidenceStep", "RunResult"]]:
    session = ExternalAgentSessionLedger(player.observatory_store.root).get_session(
        invocation.session_id
    )
    if session is None:
        raise ValueError(f"external Agent session is missing: {invocation.session_id}")
    candidates = [
        run
        for run in player.observatory_store.list_evidence_runs(limit=1000)
        if run.environment.get("external_agent_invocation_id") == invocation.id
    ]
    bundles: list[tuple["EvidenceRun", "EvidenceStep", "RunResult"]] = []
    for run in candidates:
        if run.environment.get("caller") != "ai-player-live-step":
            continue
        for step in player.observatory_store.list_evidence_steps(run.id):
            if step.action_run_id is None:
                raise ValueError("external live evidence step lacks its canonical action run")
            action_run = player.observatory_store.get_run(step.action_run_id)
            if action_run is None:
                raise ValueError(f"external live action run is missing: {step.action_run_id}")
            bundles.append((run, step, action_run))
    return sorted(
        bundles,
        key=lambda item: (item[1].action_started_at or item[1].started_at, item[1].id),
    )


def _external_metric_derivations(
    player: "AIPlayerStore",
    *,
    environment_id: str,
    evidence_step_id: str,
) -> list[Any]:
    persisted = [
        item
        for item in player.list_account_metric_derivations(environment_id, limit=1000)
        if item.after_observation.evidence_step_id == evidence_step_id
    ]
    produced = CanonicalAccountMetricProvider(player).derive_for_evidence_step(
        environment_id,
        evidence_step_id,
    )
    by_id = {item.id: item for item in persisted}
    for derivation in produced:
        existing = by_id.get(derivation.id)
        if existing is not None and existing != derivation:
            raise ValueError(f"external account metric derivation conflicts: {derivation.id}")
        by_id[derivation.id] = derivation
    metric_keys = [item.definition.metric_key for item in by_id.values()]
    if len(metric_keys) != len(set(metric_keys)):
        raise ValueError("external evidence has multiple derivations for one metric key")
    return sorted(by_id.values(), key=lambda item: (item.definition.metric_key, item.id))


def _validate_bundle(
    player: "AIPlayerStore",
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
    run: "EvidenceRun",
    step: "EvidenceStep",
    action_run: "RunResult",
) -> tuple[str, EvidenceReferenceV1, str, str]:
    environment = run.environment
    task_id = environment.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("external live evidence does not bind a canonical task")
    external = ledger.get_session(invocation.session_id)
    if external is None:
        raise ValueError(f"external Agent session is missing: {invocation.session_id}")
    expected_owner = {
        "environment_id": external.environment_id,
        "ai_player_session_id": invocation.session_id,
        "external_agent_invocation_id": invocation.id,
        "external_agent_session_id": invocation.session_id,
        "external_agent_invocation_sequence": str(invocation.sequence),
        "task_id": task_id,
    }
    if any(environment.get(key) != value for key, value in expected_owner.items()):
        raise ValueError("external live evidence ownership does not match its invocation")
    if task_id not in external.task_ids:
        raise ValueError("external live evidence task is outside the external session")
    if player.get_task(external.environment_id, task_id) is None:
        raise ValueError(f"external live evidence task is missing: {task_id}")
    if action_run.task_id != task_id:
        raise ValueError("external live action run does not bind the evidence task")
    if step.action_run_id != action_run.id:
        raise ValueError("external live evidence step does not bind its action run")
    if step.id not in run.step_ids or action_run.id not in run.action_run_ids:
        raise ValueError("external live evidence terminal bundle is incomplete")
    if step.viewport_width != run.viewport_width or step.viewport_height != run.viewport_height:
        raise ValueError("external live evidence step viewport differs from its run")
    if run.scope_id != external.environment_id:
        raise ValueError("external live evidence run scope differs from its session")
    if step.action.type in _POINTER_ACTION_TYPES:
        guard = environment.get("source_state_guard")
        if not isinstance(guard, dict) or step.target_bounds is None:
            raise ValueError("external pointer action lacks its bounded source-state guard")

    invocation_start = _timestamp(invocation.started_at)
    invocation_end = _timestamp(invocation.completed_at)
    action_start = _timestamp(step.action_started_at or step.started_at)
    action_end_value = step.action_ended_at or step.ended_at
    if action_end_value is None:
        raise ValueError("external live evidence step lacks an action end timestamp")
    action_end = _timestamp(action_end_value)
    if not (invocation_start <= action_start <= action_end <= invocation_end):
        raise ValueError("external live evidence timestamps escape the invocation")

    manifest = player.observatory_store.get_evidence_manifest(run.id)
    if manifest is None:
        raise ValueError("external live evidence manifest is missing")
    if manifest.run != run or step not in manifest.steps:
        raise ValueError("external live evidence manifest differs from canonical terminal facts")
    required_artifact_ids = set(step.artifact_ids)
    if step.before_frame_id is None or step.after_frame_id is None:
        raise ValueError("external live evidence step lacks Before/After artifacts")
    required_artifact_ids.update((step.before_frame_id, step.after_frame_id))
    artifacts = []
    for artifact_id in sorted(required_artifact_ids):
        artifact = player.observatory_store.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"external live evidence artifact is missing: {artifact_id}")
        path = Path(artifact.path)
        if not path.is_file() or _sha256(path) != artifact.sha256:
            raise ValueError(f"external live evidence artifact hash changed: {artifact_id}")
        artifacts.append(artifact)
    before = player.observatory_store.get_artifact(step.before_frame_id)
    if before is None:
        raise ValueError("external live evidence Before artifact is missing")
    reference = EvidenceReferenceV1(
        environment_id=external.environment_id,
        artifact_ids=sorted(required_artifact_ids),
        evidence_run_ids=[run.id],
        evidence_step_ids=[step.id],
        trace_run_ids=[action_run.id],
        note="外部连续 Agent 实机动作的 canonical Before/Action/After 证据。",
    )
    expected_change = environment.get("pre_execution_expectation")
    if not isinstance(expected_change, str) or not expected_change.strip():
        raise ValueError("external live evidence lacks its pre-execution expectation")
    return task_id, reference, expected_change, before.sha256


def produce_external_action_quality_samples(
    player: "AIPlayerStore",
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> list[ActionQualitySampleV1]:
    """Build all future external action samples for exactly one committed invocation."""

    invocations = ledger.list_invocations(invocation.session_id)
    canonical_invocation = next((item for item in invocations if item.id == invocation.id), None)
    if canonical_invocation is None or canonical_invocation != invocation:
        raise ValueError("external Agent invocation is not the committed canonical record")
    bundles = _invocation_steps(player, invocation)
    if not bundles:
        return []
    usage_increment = _verified_usage_increment(ledger, invocation)
    tokens_are_attributable = len(bundles) == 1 and usage_increment is not None
    tokens_are_shared = len(bundles) > 1 and usage_increment is not None
    samples: list[ActionQualitySampleV1] = []
    previous_terminal = invocation.started_at
    for run, step, action_run in bundles:
        task_id, reference, expected_change, before_sha256 = _validate_bundle(
            player,
            ledger,
            invocation,
            run,
            step,
            action_run,
        )
        action_started_at = step.action_started_at or step.started_at
        decision_latency_ms = max(
            0,
            round(
                (_timestamp(action_started_at) - _timestamp(previous_terminal)).total_seconds()
                * 1000
            ),
        )
        previous_terminal = step.ended_at or step.action_ended_at or action_started_at
        evaluation = step.live_evaluation
        expectation_measured = evaluation is not None
        expectation_matched = evaluation.expectation_met if evaluation is not None else None
        expectation_kind = run.environment.get("expected_change_kind")
        evidence_complete = bool(
            run.status in {"passed", "failed"}
            and step.status in {"passed", "failed"}
            and action_run.status in {"passed", "failed"}
            and run.ended_at
            and step.ended_at
            and action_run.ended_at
        )
        confirmed_change = bool(
            evidence_complete and expectation_matched is True and action_run.status == "passed"
        )
        measured_no_effect = bool(
            evidence_complete and expectation_matched is False and action_run.status == "passed"
        )
        outcome = (
            "confirmed" if confirmed_change else "no_effect" if measured_no_effect else "failed"
        )
        command_id = _stable_command_id(
            invocation,
            task_id=task_id,
            evidence_step_id=step.id,
        )
        sample = ActionQualitySampleV1(
            id=stable_external_action_quality_sample_id(command_id),
            environment_id=run.scope_id or run.environment["environment_id"],
            evidence_refs=[reference],
            session_id=invocation.session_id,
            task_id=task_id,
            semantic_state_id=None,
            command_id=command_id,
            action_run_id=action_run.id,
            evidence_step_id=step.id,
            decision_mode="known_state",
            execution_disposition="executed",
            preflight_disposition=(
                "passed" if step.action.type in _POINTER_ACTION_TYPES else "not_applicable"
            ),
            outcome=outcome,
            expected_change=expected_change,
            expected_change_measurement_status=(
                "measured" if expectation_measured else "unavailable"
            ),
            expected_change_matched=expectation_matched,
            adapter_call_count=1,
            invalid_target_execution=False,
            policy_violation=False,
            evidence_complete=evidence_complete,
            meaningful_change=confirmed_change and expectation_kind == "visual_change",
            task_progress=False,
            objective_completed=False,
            information_gain_units=0,
            new_state_count=0,
            new_transition_count=0,
            target_cluster_id=_target_cluster_id(step, before_sha256),
            prior_cluster_failures=0,
            recovery_succeeded=False,
            task_queue_falsely_empty=False,
            token_measurement_status=(
                "measured"
                if tokens_are_attributable
                else "shared_batch"
                if tokens_are_shared
                else "unavailable"
            ),
            model_input_tokens=(
                usage_increment.input_tokens
                if tokens_are_attributable and usage_increment
                else None
            ),
            model_output_tokens=(
                usage_increment.output_tokens
                if tokens_are_attributable and usage_increment
                else None
            ),
            model_usage_group_id=(invocation.id if tokens_are_shared else None),
            model_usage_group_action_count=(len(bundles) if tokens_are_shared else None),
            model_usage_group_input_tokens=(
                usage_increment.input_tokens if tokens_are_shared and usage_increment else None
            ),
            model_usage_group_output_tokens=(
                usage_increment.output_tokens if tokens_are_shared and usage_increment else None
            ),
            decision_latency_ms=decision_latency_ms,
            created_at=step.ended_at or step.action_ended_at or step.started_at,
        )
        derivations = _external_metric_derivations(
            player,
            environment_id=sample.environment_id,
            evidence_step_id=step.id,
        )
        samples.append(
            attach_account_metric_derivations(
                player.observatory_store,
                sample,
                derivations,
            )
        )
    return samples


def validate_external_action_quality_sample(
    player: "AIPlayerStore",
    sample: ActionQualitySampleV1,
    *,
    evidence_run: "EvidenceRun",
) -> None:
    invocation_id = evidence_run.environment.get("external_agent_invocation_id")
    invocation_session_id = evidence_run.environment.get("external_agent_session_id")
    sequence = evidence_run.environment.get("external_agent_invocation_sequence")
    if not isinstance(invocation_id, str) or invocation_session_id != sample.session_id:
        raise ValueError("external action-quality evidence lacks canonical invocation identity")
    try:
        invocation_sequence = int(sequence)
    except (TypeError, ValueError) as exc:
        raise ValueError("external action-quality invocation sequence is invalid") from exc
    ledger = ExternalAgentSessionLedger(player.observatory_store.root)
    invocation = next(
        (
            item
            for item in ledger.list_invocations(sample.session_id)
            if item.sequence == invocation_sequence and item.id == invocation_id
        ),
        None,
    )
    if invocation is None:
        raise ValueError(f"external action-quality invocation is missing: {invocation_id}")
    expected = next(
        (
            item
            for item in produce_external_action_quality_samples(player, ledger, invocation)
            if item.evidence_step_id == sample.evidence_step_id
        ),
        None,
    )
    if expected is None:
        raise ValueError("external action-quality evidence step is outside its invocation")
    if expected != sample:
        differing = sorted(
            key
            for key, value in expected.model_dump(mode="json", by_alias=True).items()
            if sample.model_dump(mode="json", by_alias=True).get(key) != value
        )
        raise ValueError(
            "external action-quality sample differs from canonical recomputation: "
            + ", ".join(differing)
        )


def persist_external_action_quality_samples(
    player: "AIPlayerStore",
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> list[ActionQualitySampleV1]:
    """Persist this invocation and feed the canonical continuous-review window."""

    samples = produce_external_action_quality_samples(player, ledger, invocation)
    monitor = PlayerIterationMonitor(player)
    for sample in samples:
        derivations = _external_metric_derivations(
            player,
            environment_id=sample.environment_id,
            evidence_step_id=str(sample.evidence_step_id),
        )
        if sample.account_metric_deltas != [item.delta for item in derivations]:
            raise ValueError(
                "external action-quality metric projection differs from canonical evidence"
            )
        for derivation in derivations:
            existing_derivation = player.get_account_metric_derivation(
                sample.environment_id,
                derivation.id,
            )
            if existing_derivation is None:
                player.append_account_metric_derivation(derivation)
            elif existing_derivation != derivation:
                raise ValueError(f"external account metric derivation conflicts: {derivation.id}")
        existing = player.get_action_quality_sample(sample.environment_id, sample.id)
        if existing is not None:
            if existing != sample:
                raise ValueError(f"external action-quality sample id conflicts: {sample.id}")
            continue
        monitor.record(sample)
    return samples


__all__ = [
    "count_confirmed_external_action_effects",
    "persist_external_action_quality_samples",
    "produce_external_action_quality_samples",
    "stable_external_action_quality_sample_id",
    "validate_external_action_quality_sample",
    "verified_external_usage_increment",
]
