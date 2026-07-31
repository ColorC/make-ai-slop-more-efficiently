"""Invocation-scoped cleanup for provider processes killed by the external Agent runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..evidence import EvidenceRecorder
from ..gateway import DeviceGateway
from ..store import ObservatoryStore
from .contracts import EvidenceReferenceV1
from .external_agent_runtime import ExternalAgentTimedOutProcessV1
from .session_control import (
    AIPlayerSessionBudgetCorrectionCommand,
    AIPlayerSessionControl,
    AIPlayerSessionError,
)
from .store import AIPlayerStore


@dataclass(frozen=True)
class ExternalAgentTimeoutCleanupResult:
    stopped_evidence_run_ids: tuple[str, ...]
    released_device_lease_ids: tuple[str, ...]
    refunded_action_step_ids: tuple[str, ...]
    budget_correction_errors: tuple[str, ...]


class ExternalAgentTimeoutResourceCleanup:
    """Settle only resources carrying the exact timed-out invocation ownership tag."""

    def __init__(
        self,
        root: Path,
        *,
        environment_id: str,
        session_id: str,
    ) -> None:
        self.root = Path(root)
        self.environment_id = environment_id
        self.session_id = session_id

    def __call__(
        self,
        timed_out: ExternalAgentTimedOutProcessV1,
    ) -> ExternalAgentTimeoutCleanupResult:
        if timed_out.session_id != self.session_id:
            raise ValueError("timed-out provider session does not match cleanup ownership")

        store = ObservatoryStore(self.root)
        reason = (
            "external Agent provider process tree ended after turn timeout: "
            f"{timed_out.invocation_id}"
        )
        owned_context = {
            "external_agent_invocation_id": timed_out.invocation_id,
            "external_agent_session_id": timed_out.session_id,
            "external_agent_invocation_sequence": str(timed_out.sequence),
            "environment_id": self.environment_id,
            "ai_player_session_id": self.session_id,
        }

        stopped_run_ids: list[str] = []
        refundable_steps = []
        recorder = EvidenceRecorder(store, None)
        session_control = AIPlayerSessionControl(AIPlayerStore(store))
        reserved_step_ids = {
            event.command_id.removeprefix("gateway-action-reserve.")
            for event in session_control.list_events(self.environment_id, self.session_id)
            if event.command_id.startswith("gateway-action-reserve.")
        }
        for run in store.list_evidence_runs(limit=1000):
            if run.status not in {"running", "paused"}:
                continue
            if any(run.environment.get(key) != value for key, value in owned_context.items()):
                continue
            refundable_steps.extend(
                step
                for step in store.list_evidence_steps(run.id)
                if step.status in {"running", "paused"}
                and step.id in reserved_step_ids
                and step.action_run_id is None
                and step.action_started_at is None
                and step.metadata.get("action_dispatch_started") is not True
            )
            recorder.stop_run(run.id, reason=reason)
            stopped_run_ids.append(run.id)

        refunded_step_ids: list[str] = []
        budget_correction_errors: list[str] = []
        for step in refundable_steps:
            current = session_control.get_session(self.environment_id, self.session_id)
            if current is None:
                budget_correction_errors.append(f"{step.id}: canonical session missing")
                continue
            if current.state != "running":
                budget_correction_errors.append(
                    f"{step.id}: canonical session is {current.state}, expected running"
                )
                continue
            corrected_remaining = min(
                current.action_budget,
                current.remaining_action_budget + 1,
            )
            if corrected_remaining <= current.remaining_action_budget:
                continue
            try:
                session_control.correct_action_budget(
                    current.id,
                    AIPlayerSessionBudgetCorrectionCommand(
                        command_id=(
                            f"{current.id}.timeout-budget-refund."
                            f"{timed_out.sequence}.{step.id}"
                        ),
                        environment_id=self.environment_id,
                        expected_version=current.version,
                        actor="external-agent-timeout-cleanup",
                        reason=(
                            "退还 provider 超时前已由 Gateway 预留、但尚未跨过"
                            f"设备动作派发边界的预算：{step.id}"
                        ),
                        expected_remaining_action_budget=current.remaining_action_budget,
                        corrected_remaining_action_budget=corrected_remaining,
                        evidence_refs=[
                            EvidenceReferenceV1(
                                environment_id=self.environment_id,
                                evidence_run_ids=[step.evidence_run_id],
                                evidence_step_ids=[step.id],
                                note="provider timeout before durable device dispatch marker",
                            )
                        ],
                    ),
                )
                refunded_step_ids.append(step.id)
            except AIPlayerSessionError as exc:
                budget_correction_errors.append(f"{step.id}: {exc.code}: {exc.message}")

        released_lease_ids: list[str] = []
        holder = f"ai-player-cli:{self.session_id}"
        gateway = DeviceGateway(store, [])
        for lease in store.list_leases():
            if lease.status != "active" or lease.holder != holder:
                continue
            if any(lease.owner_context.get(key) != value for key, value in owned_context.items()):
                continue
            released = gateway.release(lease.token)
            if released.status == "released":
                released_lease_ids.append(released.id)

        store.append_gateway_event(
            "external_agent_timeout_cleanup",
            None,
            {
                "external_agent_invocation_id": timed_out.invocation_id,
                "external_agent_session_id": timed_out.session_id,
                "external_agent_invocation_sequence": timed_out.sequence,
                "provider_process_id": timed_out.process_id,
                "provider_terminated_at": timed_out.terminated_at,
                "stopped_evidence_run_ids": stopped_run_ids,
                "released_device_lease_ids": released_lease_ids,
                "refunded_action_step_ids": refunded_step_ids,
                "budget_correction_errors": budget_correction_errors,
            },
        )
        return ExternalAgentTimeoutCleanupResult(
            stopped_evidence_run_ids=tuple(stopped_run_ids),
            released_device_lease_ids=tuple(released_lease_ids),
            refunded_action_step_ids=tuple(refunded_step_ids),
            budget_correction_errors=tuple(budget_correction_errors),
        )
