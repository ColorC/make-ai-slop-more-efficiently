"""Strict one-duty coordinator for Sanguo natural-day continuity."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .sanguo_daily_continuity import (
    DAILY_DUTIES,
    DailyDuty,
    DailyGuideReferenceV1,
    DailyTerminalEvidenceReferenceV1,
    RecordSanguoDailyDutyCommand,
    SanguoDailyContinuityDayV1,
    SanguoDailyContinuityError,
    SanguoDailyContinuityLedger,
    SanguoDailyContinuityScheduleV1,
    SealSanguoDailyContinuityCommand,
)


NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=r".*\S.*"),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class SanguoDailyDutyCandidateV1(_StrictModel):
    """An explicit semantic claim backed by already-persisted canonical records.

    The candidate deliberately has no date, day index, or expected ledger version.
    Those values come from the trusted current-day schedule at submission time, so
    callers cannot use this interface to backfill an old natural day.
    """

    schema_id: Literal["game-observatory.ai-player.sanguo-daily-duty-candidate.v1"] = Field(
        default="game-observatory.ai-player.sanguo-daily-duty-candidate.v1",
        alias="schema",
    )
    candidate_id: NonBlankText
    environment_id: NonBlankText
    continuity_run_id: NonBlankText
    duty: DailyDuty
    session_ids: list[NonBlankText] = Field(min_length=1)
    task_ids: list[NonBlankText] = Field(default_factory=list)
    guide_refs: list[DailyGuideReferenceV1] = Field(default_factory=list)
    guide_freshness_conclusion: Literal[
        "current", "unverified", "stale", "contradicted"
    ] | None = None
    guide_freshness_reason: NonBlankText | None = None
    memory_record_ids: list[NonBlankText] = Field(default_factory=list)
    evidence_refs: list[DailyTerminalEvidenceReferenceV1] = Field(min_length=1)
    summary: NonBlankText = Field(max_length=4000)
    actor: NonBlankText
    reason: NonBlankText

    @model_validator(mode="after")
    def validate_unique_ids_and_duty_shape(self) -> "SanguoDailyDutyCandidateV1":
        for field_name in ("session_ids", "task_ids", "memory_record_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        guide_fields_supplied = (
            self.guide_freshness_conclusion is not None
            and self.guide_freshness_reason is not None
        )
        if self.duty == "guide_freshness_check":
            if not self.guide_refs or not guide_fields_supplied:
                raise ValueError(
                    "guide freshness candidate requires guides, conclusion, and reason"
                )
        elif (
            self.guide_refs
            or self.guide_freshness_conclusion is not None
            or self.guide_freshness_reason is not None
        ):
            raise ValueError("guide freshness fields belong only to the guide duty")
        return self


class SanguoDailySealCandidateV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.sanguo-daily-seal-candidate.v1"] = Field(
        default="game-observatory.ai-player.sanguo-daily-seal-candidate.v1",
        alias="schema",
    )
    candidate_id: NonBlankText
    environment_id: NonBlankText
    continuity_run_id: NonBlankText
    actor: NonBlankText
    reason: NonBlankText


class SanguoDailyCoordinatorStatusV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.sanguo-daily-coordinator-status.v1"] = Field(
        default="game-observatory.ai-player.sanguo-daily-coordinator-status.v1",
        alias="schema",
    )
    schedule: SanguoDailyContinuityScheduleV1
    completed_duties: list[DailyDuty]
    event_count: int = Field(ge=0)
    can_advance: bool
    can_seal: bool


class SanguoDailyCoordinator:
    """Advances at most the next fixed duty and delegates all truth checks."""

    def __init__(self, ledger: SanguoDailyContinuityLedger) -> None:
        self.ledger = ledger

    @staticmethod
    def _identity_command_id(
        operation: Literal["advance", "seal"],
        environment_id: str,
        continuity_run_id: str,
        candidate_id: str,
    ) -> str:
        identity = json.dumps(
            {
                "operation": operation,
                "environment_id": environment_id,
                "continuity_run_id": continuity_run_id,
                "candidate_id": candidate_id,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sanguo-daily-{operation}.{hashlib.sha256(identity).hexdigest()}"

    def _existing_command_json(self, command_id: str) -> str | None:
        with self.ledger.player_store._connection() as connection:
            row = connection.execute(
                """
                SELECT command_json
                FROM ai_player_sanguo_daily_continuity_events
                WHERE command_id=?
                """,
                (command_id,),
            ).fetchone()
        return None if row is None else str(row["command_json"])

    @staticmethod
    def _candidate_payload(candidate: SanguoDailyDutyCandidateV1) -> dict[str, object]:
        return candidate.model_dump(
            mode="json",
            by_alias=False,
            exclude={"schema_id", "candidate_id"},
        )

    @staticmethod
    def _command_candidate_payload(command: RecordSanguoDailyDutyCommand) -> dict[str, object]:
        return command.model_dump(
            mode="json",
            by_alias=False,
            include={
                "environment_id",
                "continuity_run_id",
                "duty",
                "session_ids",
                "task_ids",
                "guide_refs",
                "guide_freshness_conclusion",
                "guide_freshness_reason",
                "memory_record_ids",
                "evidence_refs",
                "summary",
                "actor",
                "reason",
            },
        )

    def status(
        self,
        environment_id: str,
        continuity_run_id: str,
    ) -> SanguoDailyCoordinatorStatusV1:
        """Read the current schedule without appending a day or audit event."""

        schedule = self.ledger.schedule(environment_id, continuity_run_id)
        day = self.ledger.get_day(
            environment_id,
            continuity_run_id,
            schedule.natural_day,
        )
        events = self.ledger.list_events(
            environment_id,
            continuity_run_id,
            schedule.natural_day,
        )
        completed = [] if day is None else [item.duty for item in day.duties]
        return SanguoDailyCoordinatorStatusV1(
            schedule=schedule,
            completed_duties=completed,
            event_count=len(events),
            can_advance=(
                schedule.status in {"not_started", "in_progress"}
                and schedule.next_duty is not None
            ),
            can_seal=schedule.status == "ready_to_seal",
        )

    def advance(
        self,
        candidate: SanguoDailyDutyCandidateV1,
    ) -> SanguoDailyContinuityDayV1:
        """Commit exactly one candidate when it is today's next required duty."""

        command_id = self._identity_command_id(
            "advance",
            candidate.environment_id,
            candidate.continuity_run_id,
            candidate.candidate_id,
        )
        existing_json = self._existing_command_json(command_id)
        if existing_json is not None:
            existing = RecordSanguoDailyDutyCommand.model_validate_json(existing_json)
            if self._command_candidate_payload(existing) != self._candidate_payload(candidate):
                raise SanguoDailyContinuityError(
                    "candidate_id_conflict",
                    "candidate_id already identifies a different daily duty claim",
                )
            return self.ledger.record_duty(existing)

        schedule = self.ledger.schedule(
            candidate.environment_id,
            candidate.continuity_run_id,
        )
        if schedule.status not in {"not_started", "in_progress"}:
            raise SanguoDailyContinuityError(
                "daily_advance_unavailable",
                f"daily schedule cannot advance while status is {schedule.status}",
            )
        if candidate.duty != schedule.next_duty:
            raise SanguoDailyContinuityError(
                "duty_candidate_not_next",
                f"next duty is {schedule.next_duty}; candidate is {candidate.duty}",
            )
        command = RecordSanguoDailyDutyCommand(
            command_id=command_id,
            environment_id=candidate.environment_id,
            continuity_run_id=candidate.continuity_run_id,
            natural_day=schedule.natural_day,
            day_index=schedule.day_index,
            expected_version=schedule.expected_version,
            duty=candidate.duty,
            session_ids=candidate.session_ids,
            task_ids=candidate.task_ids,
            guide_refs=candidate.guide_refs,
            guide_freshness_conclusion=candidate.guide_freshness_conclusion,
            guide_freshness_reason=candidate.guide_freshness_reason,
            memory_record_ids=candidate.memory_record_ids,
            evidence_refs=candidate.evidence_refs,
            summary=candidate.summary,
            actor=candidate.actor,
            reason=candidate.reason,
        )
        return self.ledger.record_duty(command)

    def seal(
        self,
        candidate: SanguoDailySealCandidateV1,
    ) -> SanguoDailyContinuityDayV1:
        """Seal only today's complete day; the ledger requires real quality samples."""

        command_id = self._identity_command_id(
            "seal",
            candidate.environment_id,
            candidate.continuity_run_id,
            candidate.candidate_id,
        )
        existing_json = self._existing_command_json(command_id)
        if existing_json is not None:
            existing = SealSanguoDailyContinuityCommand.model_validate_json(existing_json)
            expected = {
                "environment_id": candidate.environment_id,
                "continuity_run_id": candidate.continuity_run_id,
                "actor": candidate.actor,
                "reason": candidate.reason,
            }
            actual = existing.model_dump(
                mode="json",
                include={"environment_id", "continuity_run_id", "actor", "reason"},
            )
            if actual != expected:
                raise SanguoDailyContinuityError(
                    "candidate_id_conflict",
                    "candidate_id already identifies a different daily seal claim",
                )
            return self.ledger.seal(existing)

        schedule = self.ledger.schedule(
            candidate.environment_id,
            candidate.continuity_run_id,
        )
        if schedule.status != "ready_to_seal":
            raise SanguoDailyContinuityError(
                "daily_seal_unavailable",
                f"daily schedule is {schedule.status}; all {len(DAILY_DUTIES)} duties are required",
            )
        return self.ledger.seal(
            SealSanguoDailyContinuityCommand(
                command_id=command_id,
                environment_id=candidate.environment_id,
                continuity_run_id=candidate.continuity_run_id,
                natural_day=schedule.natural_day,
                expected_version=schedule.expected_version,
                actor=candidate.actor,
                reason=candidate.reason,
            )
        )
