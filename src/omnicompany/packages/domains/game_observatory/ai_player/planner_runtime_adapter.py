"""AI-player planner adapter bound to the exact return of ``LLMClient.call``."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from omnicompany.runtime.llm.llm import (
    LLMCallRecord,
    use_ai_player_planner_measurement_hook,
)

from ..models import ArtifactRef
from .action_quality_producer import ActionDecisionTelemetryV1
from .orchestrator import AutonomousExecutionCommandV1
from .planner_measurement import (
    PlannerMeasurementReceiptV1,
    PlannerMeasurementSigner,
    planner_request_sha256,
)
from .store import AIPlayerStore


@dataclass(frozen=True, slots=True)
class PlannerAdapterResult:
    command: AutonomousExecutionCommandV1
    receipt: PlannerMeasurementReceiptV1
    raw_result: Any


class AIPlayerPlannerRuntimeAdapter:
    """Own the trusted signing capability and persist before returning a command."""

    def __init__(
        self,
        player_store: AIPlayerStore,
        *,
        signer: PlannerMeasurementSigner,
    ) -> None:
        self.player_store = player_store
        self.signer = signer

    def _persist_completed_invocation(
        self,
        *,
        command: AutonomousExecutionCommandV1,
        record: LLMCallRecord,
    ) -> tuple[AutonomousExecutionCommandV1, PlannerMeasurementReceiptV1]:
        if not record.invocation_id:
            raise ValueError("LLM runtime did not expose an invocation id")
        telemetry = ActionDecisionTelemetryV1(
            model_input_tokens=record.input_tokens,
            model_output_tokens=record.output_tokens,
            decision_latency_ms=max(0, round(record.latency_ms)),
        )
        artifact_id = f"artifact.ai-player.planner.{record.invocation_id}"
        command = command.model_copy(
            update={
                "decision_telemetry": telemetry,
                "planner_measurement_artifact_id": artifact_id,
            }
        )
        if command.planned_task_id is None:
            raise ValueError("planner command must bind its canonical task before return")
        receipt = PlannerMeasurementReceiptV1(
            id=f"planner-measurement.{record.invocation_id}",
            artifact_id=artifact_id,
            environment_id=command.environment_id,
            session_id=command.session_id,
            task_id=command.planned_task_id,
            command_id=command.command_id,
            planner_request_sha256=planner_request_sha256(command),
            invocation_id=record.invocation_id,
            provider="omnicompany.runtime.llm.LLMClient",
            model=record.model,
            model_input_tokens=record.input_tokens,
            model_output_tokens=record.output_tokens,
            decision_latency_ms=max(0, round(record.latency_ms)),
            completed_at=datetime.fromtimestamp(
                record.timestamp + (record.latency_ms / 1000),
                tz=timezone.utc,
            ).isoformat(),
        )
        receipt = self.signer.sign(receipt)
        raw = receipt.model_dump_json(by_alias=True).encode("utf-8")
        relative_path = Path("runtime") / "planner_measurements" / f"{receipt.id}.json"
        path = (self.player_store.observatory_store.root / relative_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("planner measurement artifact path is already occupied")
        if not path.exists():
            path.write_bytes(raw)
        artifact = ArtifactRef(
            id=artifact_id,
            kind="trace",
            path=str(relative_path),
            sha256=hashlib.sha256(raw).hexdigest(),
            captured_at=receipt.completed_at,
            media_type="application/json",
            metadata={
                "schema": receipt.schema_id,
                "environment_id": receipt.environment_id,
                "command_id": receipt.command_id,
                "producer_identity": receipt.producer_identity,
                "invocation_id": receipt.invocation_id,
            },
        )
        existing = self.player_store.observatory_store.get_artifact(artifact.id)
        if existing is not None and existing != artifact:
            raise ValueError("planner measurement artifact id is already occupied")
        if existing is None:
            self.player_store.observatory_store.save_artifact(artifact)
        self.player_store.append_planner_measurement_receipt(receipt)
        return command, receipt

    def call(
        self,
        client: Any,
        *,
        messages: list[dict[str, Any]],
        command_parser: Callable[[Any], AutonomousExecutionCommandV1],
        **call_kwargs: Any,
    ) -> PlannerAdapterResult:
        captured: dict[str, Any] = {}

        def finalize(result: Any, record: LLMCallRecord) -> None:
            parsed = command_parser(result)
            if not isinstance(parsed, AutonomousExecutionCommandV1):
                raise TypeError("planner command parser returned another contract")
            command, receipt = self._persist_completed_invocation(
                command=parsed,
                record=record,
            )
            captured.update(command=command, receipt=receipt)

        with use_ai_player_planner_measurement_hook(finalize):
            raw_result = client.call(messages, **call_kwargs)
        if "command" not in captured or "receipt" not in captured:
            raise RuntimeError("LLM planner call returned without a measurement receipt")
        return PlannerAdapterResult(
            command=captured["command"],
            receipt=captured["receipt"],
            raw_result=raw_result,
        )


__all__ = ["AIPlayerPlannerRuntimeAdapter", "PlannerAdapterResult"]
