"""Strict, append-audited natural-day ledger for the pure-AI Sanguo account."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .contracts import EvidenceReferenceV1
from .iteration_monitor import PlayerIterationMonitor
from .store import AIPlayerStore


SHANGHAI = ZoneInfo("Asia/Shanghai")
SANGUO_GAME_ID = "sanguo-mouding-tianxia"
DailyDuty = Literal[
    "post_login_coverage_audit",
    "current_goal_update",
    "guide_freshness_check",
    "reachable_business_progress",
    "end_of_day_memory_consolidation",
    "next_day_task_generation",
]
DAILY_DUTIES: tuple[DailyDuty, ...] = (
    "post_login_coverage_audit",
    "current_goal_update",
    "guide_freshness_check",
    "reachable_business_progress",
    "end_of_day_memory_consolidation",
    "next_day_task_generation",
)
DayState = Literal["in_progress", "interrupted", "sealed"]
DailyEventType = Literal["duty_recorded", "interrupted", "resumed", "sealed"]
DailyOperation = Literal["record_duty", "interrupt", "resume", "seal"]
NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=r".*\S.*"),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    @model_validator(mode="before")
    @classmethod
    def reject_blank_top_level_strings(cls, value: Any) -> Any:
        if isinstance(value, dict):
            cleaned = dict(value)
            for key, item in cleaned.items():
                if isinstance(item, str):
                    item = item.strip()
                    if not item:
                        raise ValueError(f"{key} must not be blank")
                    cleaned[key] = item
            return cleaned
        return value


def _unique_nonblank(values: list[str], name: str) -> list[str]:
    if any(not item.strip() for item in values):
        raise ValueError(f"{name} must contain non-blank ids")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")
    return values


class DailyGuideReferenceV1(_StrictModel):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)


class DailyTerminalEvidenceReferenceV1(_StrictModel):
    """Environment-free terminal evidence; the containing day supplies its environment."""

    artifact_ids: list[str] = Field(min_length=1)
    evidence_run_ids: list[str] = Field(min_length=1)
    evidence_step_ids: list[str] = Field(min_length=1)

    @field_validator("artifact_ids", "evidence_run_ids", "evidence_step_ids")
    @classmethod
    def validate_ids(cls, value: list[str], info: Any) -> list[str]:
        return _unique_nonblank(value, str(info.field_name))

    def canonical(self, environment_id: str) -> EvidenceReferenceV1:
        return EvidenceReferenceV1(
            environment_id=environment_id,
            artifact_ids=self.artifact_ids,
            evidence_run_ids=self.evidence_run_ids,
            evidence_step_ids=self.evidence_step_ids,
        )


class DailyTaskSnapshotV1(_StrictModel):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: str = Field(min_length=1)


class DailyGuideFreshnessV1(_StrictModel):
    conclusion: Literal["current", "unverified", "stale", "contradicted"]
    refresh_required: bool
    reason: NonBlankText
    checked_at: str = Field(min_length=1)


class SanguoDailyDutyCompletionV1(_StrictModel):
    duty: DailyDuty
    session_ids: list[str] = Field(min_length=1)
    task_snapshots: list[DailyTaskSnapshotV1] = Field(default_factory=list)
    guide_refs: list[DailyGuideReferenceV1] = Field(default_factory=list)
    guide_freshness: DailyGuideFreshnessV1 | None = None
    memory_record_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[DailyTerminalEvidenceReferenceV1] = Field(min_length=1)
    summary: NonBlankText = Field(max_length=4000)
    completed_at: str = Field(min_length=1)

    @field_validator("session_ids", "memory_record_ids")
    @classmethod
    def validate_id_lists(cls, value: list[str], info: Any) -> list[str]:
        return _unique_nonblank(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_duty_specific_references(self) -> "SanguoDailyDutyCompletionV1":
        if self.duty in {
            "current_goal_update",
            "reachable_business_progress",
            "next_day_task_generation",
        } and not self.task_snapshots:
            raise ValueError(f"{self.duty} requires a same-day canonical task snapshot")
        if self.duty == "guide_freshness_check" and (
            not self.guide_refs or self.guide_freshness is None
        ):
            raise ValueError(
                "guide freshness check requires canonical guides and a structured conclusion"
            )
        if self.duty != "guide_freshness_check" and self.guide_freshness is not None:
            raise ValueError("structured guide freshness belongs only to its daily duty")
        if self.duty == "end_of_day_memory_consolidation" and not self.memory_record_ids:
            raise ValueError("end-of-day consolidation requires canonical memory")
        return self


class SanguoDailyContinuityDayV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.sanguo-daily-continuity-day.v1"
    ] = Field(
        default="game-observatory.ai-player.sanguo-daily-continuity-day.v1",
        alias="schema",
    )
    environment_id: str = Field(min_length=1)
    continuity_run_id: str = Field(min_length=1)
    natural_day: date
    timezone_name: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    day_index: int = Field(ge=1, le=7)
    state: DayState = "in_progress"
    version: int = Field(ge=1)
    duties: list[SanguoDailyDutyCompletionV1] = Field(min_length=1, max_length=6)
    interruption_count: int = Field(default=0, ge=0)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    sealed_at: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_day(self) -> "SanguoDailyContinuityDayV1":
        actual = [item.duty for item in self.duties]
        if actual != list(DAILY_DUTIES[: len(actual)]):
            raise ValueError("daily duties must be recorded once in the fixed operational order")
        step_ids = [
            step_id
            for duty in self.duties
            for reference in duty.evidence_refs
            for step_id in reference.evidence_step_ids
        ]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("each daily duty requires exclusive EvidenceStep ids")
        if any(
            not {
                step_id
                for reference in duty.evidence_refs
                for step_id in reference.evidence_step_ids
            }
            for duty in self.duties
        ):
            raise ValueError("each daily duty requires at least one EvidenceStep")
        task_versions = [
            (snapshot.id, snapshot.version)
            for duty in self.duties
            for snapshot in duty.task_snapshots
        ]
        if len(task_versions) != len(set(task_versions)):
            raise ValueError("daily duties must not reuse one task version")
        for duty in self.duties:
            try:
                completed_at = datetime.fromisoformat(
                    duty.completed_at.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError("daily duty timestamp must use ISO-8601") from exc
            if completed_at.tzinfo is None or completed_at.utcoffset() is None:
                raise ValueError("daily duty timestamp must include a timezone")
            if completed_at.astimezone(SHANGHAI).date() != self.natural_day:
                raise ValueError("daily duty timestamp must fall inside its Shanghai natural day")
        if self.state == "sealed":
            if actual != list(DAILY_DUTIES) or not self.sealed_at:
                raise ValueError("a sealed day requires all six duties and sealed_at")
        elif self.sealed_at is not None:
            raise ValueError("only a sealed day may have sealed_at")
        return self


class SanguoDailyContinuityEventV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.sanguo-daily-continuity-event.v1"
    ] = Field(
        default="game-observatory.ai-player.sanguo-daily-continuity-event.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    continuity_run_id: str = Field(min_length=1)
    natural_day: date
    event_type: DailyEventType
    operation: DailyOperation
    command_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: NonBlankText
    reason: NonBlankText
    evidence_refs: list[DailyTerminalEvidenceReferenceV1] = Field(default_factory=list)
    previous_version: int = Field(ge=0)
    new_version: int = Field(ge=1)
    timestamp: str = Field(min_length=1)

    @model_validator(mode="after")
    def advance_one_version(self) -> "SanguoDailyContinuityEventV1":
        if self.new_version != self.previous_version + 1:
            raise ValueError("a daily event must advance exactly one day version")
        if self.event_type != "sealed" and not self.evidence_refs:
            raise ValueError("every non-seal daily event requires canonical evidence")
        expected_event_type: dict[DailyOperation, DailyEventType] = {
            "record_duty": "duty_recorded",
            "interrupt": "interrupted",
            "resume": "resumed",
            "seal": "sealed",
        }
        if expected_event_type[self.operation] != self.event_type:
            raise ValueError("daily event type must match its operation")
        if self.previous_version == 0 and self.previous_event_sha256 != "0" * 64:
            raise ValueError("the first daily event must begin at the zero hash")
        return self


class RecordSanguoDailyDutyCommand(_StrictModel):
    command_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    continuity_run_id: str = Field(min_length=1)
    natural_day: date
    day_index: int = Field(ge=1, le=7)
    expected_version: int = Field(ge=0)
    duty: DailyDuty
    session_ids: list[str] = Field(min_length=1)
    task_ids: list[str] = Field(default_factory=list)
    guide_refs: list[DailyGuideReferenceV1] = Field(default_factory=list)
    guide_freshness_conclusion: Literal[
        "current", "unverified", "stale", "contradicted"
    ] | None = None
    guide_freshness_reason: NonBlankText | None = None
    memory_record_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[DailyTerminalEvidenceReferenceV1] = Field(min_length=1)
    summary: NonBlankText = Field(max_length=4000)
    actor: NonBlankText
    reason: NonBlankText

    @model_validator(mode="after")
    def validate_guide_freshness_input(self) -> "RecordSanguoDailyDutyCommand":
        supplied = (
            self.guide_freshness_conclusion is not None
            and self.guide_freshness_reason is not None
        )
        if self.duty == "guide_freshness_check" and (
            not self.guide_refs or not supplied
        ):
            raise ValueError("guide freshness duty requires guides, conclusion, and reason")
        if self.duty != "guide_freshness_check" and (
            self.guide_refs
            or self.guide_freshness_conclusion is not None
            or self.guide_freshness_reason is not None
        ):
            raise ValueError("guide freshness fields belong only to the guide duty")
        return self


class SanguoDailyStateCommand(_StrictModel):
    command_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    continuity_run_id: str = Field(min_length=1)
    natural_day: date
    expected_version: int = Field(ge=1)
    session_ids: list[str] = Field(min_length=1)
    evidence_refs: list[DailyTerminalEvidenceReferenceV1] = Field(min_length=1)
    actor: NonBlankText
    reason: NonBlankText


class SealSanguoDailyContinuityCommand(_StrictModel):
    command_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    continuity_run_id: str = Field(min_length=1)
    natural_day: date
    expected_version: int = Field(ge=1)
    actor: NonBlankText
    reason: NonBlankText


class SanguoDailyContinuityAssessmentV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.sanguo-daily-continuity-assessment.v1"
    ] = Field(
        default="game-observatory.ai-player.sanguo-daily-continuity-assessment.v1",
        alias="schema",
    )
    environment_id: str
    continuity_run_id: str
    timezone_name: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    required_natural_days: Literal[7] = 7
    recorded_natural_days: int = Field(ge=0)
    sealed_natural_days: int = Field(ge=0)
    continuity_component_passed: bool
    g12_decision: Literal["not_decided_by_this_facility"] = "not_decided_by_this_facility"
    reasons: list[str]
    evaluated_at: str


class SanguoDailyContinuityScheduleV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.sanguo-daily-continuity-schedule.v1"
    ] = Field(
        default="game-observatory.ai-player.sanguo-daily-continuity-schedule.v1",
        alias="schema",
    )
    environment_id: NonBlankText
    continuity_run_id: NonBlankText
    natural_day: date
    day_index: int = Field(ge=1, le=7)
    status: Literal[
        "not_started",
        "in_progress",
        "interrupted",
        "ready_to_seal",
        "sealed",
        "completed",
        "blocked",
    ]
    next_duty: DailyDuty | None = None
    expected_version: int = Field(ge=0)
    reasons: list[NonBlankText] = Field(default_factory=list)
    evaluated_at: str


class SanguoDailyContinuityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SanguoDailyContinuityLedger:
    """Transactional day ledger; it never calls a game or device adapter."""

    def __init__(
        self,
        player_store: AIPlayerStore,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.player_store = player_store
        self._now_provider = now_provider
        self._anchor_root = (
            Path(self.player_store.observatory_store.root)
            / "ai_player_daily_continuity_anchors"
        )

    def _now(self, environment_id: str) -> datetime:
        environment = self._validate_environment(environment_id)
        fixture_channel = environment.channel.casefold() in {
            "fixture",
            "test",
            "testing",
        }
        if self._now_provider is not None and not fixture_channel:
            raise SanguoDailyContinuityError(
                "untrusted_clock_override",
                "正式账号环境禁止自定义时钟；自然日必须来自数据库系统时钟。",
            )
        if self._now_provider is not None:
            now = self._now_provider()
        else:
            with self.player_store._connection() as connection:
                row = connection.execute(
                    "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                ).fetchone()
            now = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("daily continuity clock must be timezone-aware")
        return now

    @staticmethod
    def _json(model: BaseModel) -> str:
        return json.dumps(
            model.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _request_hash(operation: str, command: BaseModel) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "operation": operation,
                    "command": command.model_dump(mode="json", by_alias=True),
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _event_hash(
        event_without_hash: dict[str, Any], command_json: str, result_day_json: str
    ) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "event_without_hash": event_without_hash,
                    "command_json": command_json,
                    "result_day_json": result_day_json,
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _event_id(command_id: str) -> str:
        digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:32]
        return f"sanguo-daily-event.{digest}"

    def _anchor_path(
        self, environment_id: str, continuity_run_id: str, natural_day: date
    ) -> Path:
        scope = hashlib.sha256(
            f"{environment_id}\0{continuity_run_id}".encode("utf-8")
        ).hexdigest()
        return self._anchor_root / scope / f"{natural_day.isoformat()}.jsonl"

    def _read_anchors(
        self, environment_id: str, continuity_run_id: str, natural_day: date
    ) -> list[dict[str, Any]]:
        path = self._anchor_path(environment_id, continuity_run_id, natural_day)
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise SanguoDailyContinuityError(
                    "invalid_external_anchor", "日账数据库外锚点格式损坏。"
                )
            records.append(value)
        return records

    def _append_anchor(self, event: SanguoDailyContinuityEventV1) -> None:
        path = self._anchor_path(
            event.environment_id, event.continuity_run_id, event.natural_day
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event_id": event.id,
            "new_version": event.new_version,
            "previous_event_sha256": event.previous_event_sha256,
            "event_sha256": event.event_sha256,
            "anchored_at": event.timestamp,
        }
        existing = self._read_anchors(
            event.environment_id, event.continuity_run_id, event.natural_day
        )
        matching = [item for item in existing if item.get("event_id") == event.id]
        if matching:
            if matching != [record]:
                raise SanguoDailyContinuityError(
                    "external_anchor_conflict", "同一日账事件的数据库外锚点发生冲突。"
                )
            return
        expected_version = len(existing) + 1
        if event.new_version != expected_version:
            raise SanguoDailyContinuityError(
                "external_anchor_gap", "数据库外锚点版本链不连续。"
            )
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _timestamp(now: datetime) -> str:
        return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _assert_today(self, natural_day: date, now: datetime) -> None:
        current_day = now.astimezone(SHANGHAI).date()
        if natural_day != current_day:
            direction = "future" if natural_day > current_day else "past"
            raise SanguoDailyContinuityError(
                f"{direction}_natural_day",
                "日账只能由当天真实运行写入，不能补写过去或预写未来日期。",
            )

    def _validate_environment(self, environment_id: str) -> Any:
        environment = self.player_store.get_environment(environment_id)
        if environment is None:
            raise SanguoDailyContinuityError("unknown_environment", "日账环境不存在。")
        if SANGUO_GAME_ID not in {environment.game_id, *environment.game_id_aliases}:
            raise SanguoDailyContinuityError(
                "wrong_game", "连续经营日账只接受《三国：谋定天下》环境。"
            )
        return environment

    @staticmethod
    def _parse_timestamp(value: str, label: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SanguoDailyContinuityError(
                "invalid_timestamp", f"{label} 不是合法 ISO-8601 时间。"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SanguoDailyContinuityError(
                "invalid_timestamp", f"{label} 必须包含时区。"
            )
        return parsed

    def _task_snapshots(
        self,
        environment_id: str,
        task_ids: Sequence[str],
        *,
        natural_day: date,
    ) -> list[DailyTaskSnapshotV1]:
        snapshots: list[DailyTaskSnapshotV1] = []
        with self.player_store._connection() as connection:
            for task_id in task_ids:
                row = connection.execute(
                    """
                    SELECT version, body_json, updated_at
                    FROM ai_player_frontier_tasks
                    WHERE environment_id=? AND id=?
                    """,
                    (environment_id, task_id),
                ).fetchone()
                if row is None:
                    raise SanguoDailyContinuityError(
                        "task_environment_mismatch", f"任务不属于日账环境：{task_id}"
                    )
                updated = self._parse_timestamp(row["updated_at"], f"任务 {task_id} 更新时间")
                if updated.astimezone(SHANGHAI).date() != natural_day:
                    raise SanguoDailyContinuityError(
                        "task_not_changed_today",
                        f"任务 {task_id} 不是当天新建或更新的版本。",
                    )
                snapshots.append(
                    DailyTaskSnapshotV1(
                        id=task_id,
                        version=int(row["version"]),
                        body_sha256=hashlib.sha256(
                            row["body_json"].encode("utf-8")
                        ).hexdigest(),
                        updated_at=row["updated_at"],
                    )
                )
        if len({(item.id, item.version) for item in snapshots}) != len(snapshots):
            raise SanguoDailyContinuityError(
                "duplicate_task_snapshot", "同一职责不能重复引用同一任务版本。"
            )
        return snapshots

    def _guide_freshness(
        self,
        environment_id: str,
        guide_refs: Sequence[DailyGuideReferenceV1],
        *,
        supplied_conclusion: str | None,
        supplied_reason: str | None,
        now: datetime,
    ) -> DailyGuideFreshnessV1 | None:
        if not guide_refs:
            return None
        statuses: list[str] = []
        for guide_ref in guide_refs:
            guide = self.player_store.get_guide_knowledge(
                environment_id, guide_ref.id, version=guide_ref.version
            )
            if guide is None:
                raise SanguoDailyContinuityError(
                    "guide_environment_mismatch",
                    f"攻略知识不存在或不属于日账环境：{guide_ref.id}@{guide_ref.version}",
                )
            eligible_versions = [
                candidate
                for candidate in self.player_store.list_guide_knowledge(environment_id)
                if candidate.id == guide_ref.id
                and self._parse_timestamp(
                    candidate.created_at,
                    f"攻略知识 {candidate.id}@{candidate.version} 建立时间",
                )
                <= now
            ]
            latest_version = max(
                (candidate.version for candidate in eligible_versions),
                default=None,
            )
            if latest_version != guide_ref.version:
                raise SanguoDailyContinuityError(
                    "guide_version_not_latest",
                    f"攻略知识必须引用检查时已经存在的最新版本："
                    f"{guide_ref.id}@{latest_version}。",
                )
            status = guide.status
            if status == "current" and guide.fresh_until is not None and guide.fresh_until < now:
                status = "stale"
            statuses.append(status)
        conclusion = (
            "contradicted"
            if "contradicted" in statuses
            else "stale"
            if "stale" in statuses
            else "current"
            if statuses and all(status == "current" for status in statuses)
            else "unverified"
        )
        if supplied_conclusion != conclusion:
            raise SanguoDailyContinuityError(
                "guide_freshness_mismatch",
                f"结构化攻略新鲜度结论应为 {conclusion}。",
            )
        if supplied_reason is None:
            raise SanguoDailyContinuityError(
                "guide_freshness_reason_missing", "攻略新鲜度检查必须写明结论依据。"
            )
        return DailyGuideFreshnessV1(
            conclusion=conclusion,
            refresh_required=conclusion != "current",
            reason=supplied_reason,
            checked_at=self._timestamp(now),
        )

    def _validate_links(
        self,
        environment_id: str,
        *,
        natural_day: date,
        now: datetime,
        session_ids: Sequence[str],
        task_snapshots: Sequence[DailyTaskSnapshotV1] = (),
        guide_refs: Sequence[DailyGuideReferenceV1] = (),
        memory_record_ids: Sequence[str] = (),
        evidence_refs: Sequence[DailyTerminalEvidenceReferenceV1],
        already_used_step_ids: set[str] | None = None,
    ) -> None:
        canonical_evidence_refs = [
            reference.canonical(environment_id) for reference in evidence_refs
        ]
        with self.player_store._connection() as connection:
            for session_id in session_ids:
                row = connection.execute(
                    "SELECT environment_id, body_json FROM ai_player_sessions WHERE id=?",
                    (session_id,),
                ).fetchone()
                if row is None or row["environment_id"] != environment_id:
                    raise SanguoDailyContinuityError(
                        "session_environment_mismatch", f"会话不属于日账环境：{session_id}"
                    )
                session_payload = json.loads(row["body_json"])
                started_at = session_payload.get("started_at") or session_payload.get("created_at")
                if not started_at or self._parse_timestamp(
                    started_at, f"会话 {session_id} 开始时间"
                ).astimezone(SHANGHAI).date() != natural_day:
                    raise SanguoDailyContinuityError(
                        "session_not_started_today",
                        f"会话 {session_id} 不是当天登录后建立的会话。",
                    )
        for snapshot in task_snapshots:
            row = None
            with self.player_store._connection() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM ai_player_entity_evidence
                    WHERE environment_id=? AND entity_type='frontier_task'
                      AND entity_id=? AND entity_version=? LIMIT 1
                    """,
                    (environment_id, snapshot.id, str(snapshot.version)),
                ).fetchone()
            if row is None:
                raise SanguoDailyContinuityError(
                    "task_version_unproven",
                    f"任务版本缺少 canonical 留证：{snapshot.id}@{snapshot.version}",
                )
        for guide_ref in guide_refs:
            if self.player_store.get_guide_knowledge(
                environment_id, guide_ref.id, version=guide_ref.version
            ) is None:
                raise SanguoDailyContinuityError(
                    "guide_environment_mismatch",
                    f"攻略知识不存在或不属于日账环境：{guide_ref.id}@{guide_ref.version}",
                )
        for memory_id in memory_record_ids:
            memory = self.player_store.get_memory(environment_id, memory_id)
            if memory is None:
                raise SanguoDailyContinuityError(
                    "memory_environment_mismatch", f"记忆不属于日账环境：{memory_id}"
                )
            memory_day = self._parse_timestamp(
                memory.created_at, f"记忆 {memory_id} 创建时间"
            ).astimezone(SHANGHAI).date()
            if memory_day != natural_day or memory.status != "active":
                raise SanguoDailyContinuityError(
                    "memory_not_created_today",
                    f"记忆 {memory_id} 不是当天新增的有效版本。",
                )
        try:
            resolved_evidence = self.player_store.resolve_evidence_references(
                canonical_evidence_refs
            )
        except (KeyError, ValueError) as exc:
            raise SanguoDailyContinuityError(
                "invalid_evidence_reference", f"日账证据无法解析：{exc}"
            ) from exc
        all_run_ids = {
            run_id for reference in evidence_refs for run_id in reference.evidence_run_ids
        }
        all_step_ids = {
            step_id for reference in evidence_refs for step_id in reference.evidence_step_ids
        }
        if not all_step_ids or not all_run_ids:
            raise SanguoDailyContinuityError(
                "terminal_step_missing",
                "每项日账职责都必须绑定 EvidenceRun 与终态通过的 EvidenceStep。",
            )
        used = already_used_step_ids or set()
        if all_step_ids.intersection(used):
            raise SanguoDailyContinuityError(
                "evidence_step_reused",
                "同一自然日的职责和恢复事件不能复用已经占用的 EvidenceStep。",
            )
        for step_id in all_step_ids:
            step = self.player_store.observatory_store.get_evidence_step(step_id)
            if step is None or step.status != "passed" or not step.ended_at:
                raise SanguoDailyContinuityError(
                    "terminal_step_not_passed", f"EvidenceStep 未终态通过：{step_id}"
                )
            ended_at = self._parse_timestamp(step.ended_at, f"EvidenceStep {step_id} 结束时间")
            if ended_at > now or ended_at.astimezone(SHANGHAI).date() != natural_day:
                raise SanguoDailyContinuityError(
                    "terminal_step_wrong_day", f"EvidenceStep 不属于当天：{step_id}"
                )
            if not step.after_frame_id or not step.stability.settled:
                raise SanguoDailyContinuityError(
                    "terminal_step_unsettled", f"EvidenceStep 没有稳定终态画面：{step_id}"
                )
            if step.terminal_condition is not None and (
                step.terminal_evaluation is None or not step.terminal_evaluation.passed
            ):
                raise SanguoDailyContinuityError(
                    "terminal_condition_failed", f"EvidenceStep 终态条件未通过：{step_id}"
                )
            if step.evidence_run_id not in all_run_ids:
                raise SanguoDailyContinuityError(
                    "step_run_unbound", f"EvidenceStep 未绑定所属 EvidenceRun：{step_id}"
                )
            run = self.player_store.observatory_store.get_evidence_run(step.evidence_run_id)
            if run is None or run.status != "passed" or not run.ended_at:
                raise SanguoDailyContinuityError(
                    "evidence_run_not_passed", f"EvidenceRun 未终态通过：{step.evidence_run_id}"
                )
            run_ended = self._parse_timestamp(
                run.ended_at, f"EvidenceRun {run.id} 结束时间"
            )
            run_started = self._parse_timestamp(
                run.started_at, f"EvidenceRun {run.id} 开始时间"
            )
            if run_ended > now or run_ended.astimezone(SHANGHAI).date() != natural_day:
                raise SanguoDailyContinuityError(
                    "evidence_run_wrong_day", f"EvidenceRun 不属于当天：{run.id}"
                )
            if step.id not in run.step_ids:
                raise SanguoDailyContinuityError(
                    "step_absent_from_run", f"EvidenceRun 未列出 EvidenceStep：{step.id}"
                )
            referenced_artifact_ids = {
                artifact_id
                for reference in evidence_refs
                if step.id in reference.evidence_step_ids
                for artifact_id in reference.artifact_ids
            }
            required_capture_ids = {step.before_frame_id, step.after_frame_id}
            if None in required_capture_ids or not required_capture_ids.issubset(
                referenced_artifact_ids
            ):
                raise SanguoDailyContinuityError(
                    "terminal_frames_unbound",
                    f"EvidenceStep 的 Before/After 原图未完整绑定到日账：{step.id}",
                )
            if not required_capture_ids.issubset(set(step.artifact_ids)):
                raise SanguoDailyContinuityError(
                    "terminal_frames_absent_from_step",
                    f"EvidenceStep 未保留其 Before/After 原图：{step.id}",
                )
            if not required_capture_ids.issubset(set(run.artifact_ids)):
                raise SanguoDailyContinuityError(
                    "terminal_frames_absent_from_run",
                    f"EvidenceRun 未保留该步骤的 Before/After 原图：{run.id}",
                )
            resolved_artifacts = {
                artifact.id: artifact for artifact in resolved_evidence["artifact"]
            }
            for artifact_id in required_capture_ids:
                artifact = resolved_artifacts.get(artifact_id)
                if artifact is None or not artifact.captured_at:
                    raise SanguoDailyContinuityError(
                        "artifact_capture_time_missing",
                        f"日账原图缺少独立采集时间：{artifact_id}",
                    )
                expected_role = (
                    "before" if artifact_id == step.before_frame_id else "after"
                )
                if (
                    artifact.metadata.get("evidence_run_id") != run.id
                    or artifact.metadata.get("evidence_step_id") != step.id
                    or artifact.metadata.get("evidence_role") != expected_role
                ):
                    raise SanguoDailyContinuityError(
                        "artifact_evidence_identity_mismatch",
                        f"日账原图未精确绑定该 EvidenceRun、EvidenceStep 与画面角色：{artifact_id}",
                    )
                captured_at = self._parse_timestamp(
                    artifact.captured_at, f"ArtifactRef {artifact_id} 采集时间"
                )
                if (
                    captured_at > now
                    or captured_at.astimezone(SHANGHAI).date() != natural_day
                    or captured_at < run_started
                    or captured_at > run_ended
                ):
                    raise SanguoDailyContinuityError(
                        "artifact_capture_time_mismatch",
                        f"日账原图不是该 EvidenceRun 当天实时采集：{artifact_id}",
                    )
            embedded = run.environment or {}
            if embedded.get("environment_id") != environment_id:
                raise SanguoDailyContinuityError(
                    "run_environment_mismatch", f"EvidenceRun 未精确绑定日账环境：{run.id}"
                )
            embedded_session_id = (
                embedded.get("ai_player_session_id")
                or embedded.get("session_id")
            )
            if embedded_session_id not in set(session_ids):
                raise SanguoDailyContinuityError(
                    "run_session_mismatch", f"EvidenceRun 未精确绑定所列 session：{run.id}"
                )
        if memory_record_ids:
            for memory_id in memory_record_ids:
                memory = self.player_store.get_memory(environment_id, memory_id)
                assert memory is not None
                memory_steps = {
                    step_id
                    for reference in memory.evidence_refs
                    for step_id in reference.evidence_step_ids
                }
                if not memory_steps.intersection(all_step_ids):
                    raise SanguoDailyContinuityError(
                        "memory_missing_daily_evidence",
                        f"当天记忆未由该职责的当天 EvidenceStep 支持：{memory_id}",
                    )

    def _idempotent_result(
        self,
        connection: sqlite3.Connection,
        *,
        command_id: str,
        request_sha256: str,
    ) -> SanguoDailyContinuityDayV1 | None:
        row = connection.execute(
            """
            SELECT request_sha256, result_day_json, environment_id,
                   continuity_run_id, natural_day, id, new_version,
                   previous_event_sha256, event_sha256
            FROM ai_player_sanguo_daily_continuity_events WHERE command_id=?
            """,
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != request_sha256:
            raise SanguoDailyContinuityError(
                "idempotency_conflict", "这个 command_id 已用于不同的日账命令。"
            )
        anchors = self._read_anchors(
            row["environment_id"], row["continuity_run_id"], date.fromisoformat(row["natural_day"])
        )
        if not any(
            item.get("event_id") == row["id"]
            and item.get("new_version") == row["new_version"]
            and item.get("previous_event_sha256") == row["previous_event_sha256"]
            and item.get("event_sha256") == row["event_sha256"]
            for item in anchors
        ):
            raise SanguoDailyContinuityError(
                "external_anchor_missing",
                "日账命令虽在数据库中存在，但缺少匹配的数据库外锚点，已拒绝重放。",
            )
        return SanguoDailyContinuityDayV1.model_validate_json(row["result_day_json"])

    def _build_event(
        self,
        connection: sqlite3.Connection,
        *,
        command: BaseModel,
        operation: DailyOperation,
        event_type: DailyEventType,
        request_sha256: str,
        result: SanguoDailyContinuityDayV1,
        previous_version: int,
        timestamp: str,
        evidence_refs: Sequence[DailyTerminalEvidenceReferenceV1] = (),
    ) -> SanguoDailyContinuityEventV1:
        previous_row = connection.execute(
            """
            SELECT event_sha256 FROM ai_player_sanguo_daily_continuity_events
            WHERE environment_id=? AND continuity_run_id=? AND natural_day=?
            ORDER BY new_version DESC LIMIT 1
            """,
            (
                result.environment_id,
                result.continuity_run_id,
                result.natural_day.isoformat(),
            ),
        ).fetchone()
        previous_hash = (
            previous_row["event_sha256"] if previous_row is not None else "0" * 64
        )
        if previous_hash is None:
            raise SanguoDailyContinuityError(
                "legacy_event_unanchored",
                "旧日账事件没有完整哈希链，必须重新开始连续经营批次。",
            )
        payload = {
            "id": self._event_id(str(getattr(command, "command_id"))),
            "environment_id": result.environment_id,
            "continuity_run_id": result.continuity_run_id,
            "natural_day": result.natural_day.isoformat(),
            "event_type": event_type,
            "operation": operation,
            "command_id": str(getattr(command, "command_id")),
            "request_sha256": request_sha256,
            "previous_event_sha256": previous_hash,
            "actor": str(getattr(command, "actor")),
            "reason": str(getattr(command, "reason")),
            "evidence_refs": [
                item.model_dump(mode="json", by_alias=True) for item in evidence_refs
            ],
            "previous_version": previous_version,
            "new_version": result.version,
            "timestamp": timestamp,
        }
        command_json = self._json(command)
        result_json = self._json(result)
        event_sha256 = self._event_hash(payload, command_json, result_json)
        return SanguoDailyContinuityEventV1(
            **payload,
            event_sha256=event_sha256,
        )

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        event: SanguoDailyContinuityEventV1,
        *,
        command: BaseModel,
        result: SanguoDailyContinuityDayV1,
        evidence_refs: Sequence[DailyTerminalEvidenceReferenceV1] = (),
    ) -> None:
        result_json = self._json(result)
        connection.execute(
            """
            INSERT INTO ai_player_sanguo_daily_continuity_events(
                id, environment_id, continuity_run_id, natural_day, event_type,
                command_id, request_sha256, previous_version, new_version,
                body_json, result_day_json, created_at, operation, command_json,
                previous_event_sha256, event_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event.id,
                event.environment_id,
                event.continuity_run_id,
                event.natural_day.isoformat(),
                event.event_type,
                event.command_id,
                event.request_sha256,
                event.previous_version,
                event.new_version,
                self._json(event),
                result_json,
                event.timestamp,
                event.operation,
                self._json(command),
                event.previous_event_sha256,
                event.event_sha256,
            ),
        )
        if evidence_refs:
            self.player_store._record_evidence(
                connection,
                event.environment_id,
                "sanguo_daily_continuity_event",
                event.id,
                str(event.new_version),
                [reference.canonical(event.environment_id) for reference in evidence_refs],
            )

    def _get_day_in_connection(
        self,
        connection: sqlite3.Connection,
        environment_id: str,
        continuity_run_id: str,
        natural_day: date,
    ) -> SanguoDailyContinuityDayV1 | None:
        row = connection.execute(
            """
            SELECT body_json FROM ai_player_sanguo_daily_continuity_days
            WHERE environment_id=? AND continuity_run_id=? AND natural_day=?
            """,
            (environment_id, continuity_run_id, natural_day.isoformat()),
        ).fetchone()
        return SanguoDailyContinuityDayV1.model_validate_json(row["body_json"]) if row else None

    def get_day(
        self, environment_id: str, continuity_run_id: str, natural_day: date
    ) -> SanguoDailyContinuityDayV1 | None:
        with self.player_store._connection() as connection:
            return self._get_day_in_connection(
                connection, environment_id, continuity_run_id, natural_day
            )

    def list_days(
        self, environment_id: str, continuity_run_id: str
    ) -> list[SanguoDailyContinuityDayV1]:
        with self.player_store._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_sanguo_daily_continuity_days
                WHERE environment_id=? AND continuity_run_id=?
                ORDER BY day_index, natural_day
                """,
                (environment_id, continuity_run_id),
            ).fetchall()
        return [SanguoDailyContinuityDayV1.model_validate_json(row["body_json"]) for row in rows]

    def list_run_ids(self, environment_id: str) -> list[str]:
        self._validate_environment(environment_id)
        with self.player_store._connection() as connection:
            rows = connection.execute(
                """
                SELECT continuity_run_id, MAX(natural_day) AS latest_day
                FROM ai_player_sanguo_daily_continuity_days
                WHERE environment_id=?
                GROUP BY continuity_run_id
                ORDER BY latest_day DESC, continuity_run_id
                """,
                (environment_id,),
            ).fetchall()
        return [str(row["continuity_run_id"]) for row in rows]

    def schedule(
        self, environment_id: str, continuity_run_id: str
    ) -> SanguoDailyContinuityScheduleV1:
        """Return the fail-closed scheduler gate without performing game actions."""

        now = self._now(environment_id)
        today = now.astimezone(SHANGHAI).date()
        self._validate_environment(environment_id)
        try:
            days = self.list_days(environment_id, continuity_run_id)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            return SanguoDailyContinuityScheduleV1(
                environment_id=environment_id,
                continuity_run_id=continuity_run_id,
                natural_day=today,
                day_index=1,
                status="blocked",
                expected_version=0,
                reasons=[f"日账快照无法解析：{type(exc).__name__}。"],
                evaluated_at=self._timestamp(now),
            )
        if not days:
            return SanguoDailyContinuityScheduleV1(
                environment_id=environment_id,
                continuity_run_id=continuity_run_id,
                natural_day=today,
                day_index=1,
                status="not_started",
                next_duty=DAILY_DUTIES[0],
                expected_version=0,
                evaluated_at=self._timestamp(now),
            )
        last = days[-1]
        if last.day_index == 7 and last.state == "sealed":
            return SanguoDailyContinuityScheduleV1(
                environment_id=environment_id,
                continuity_run_id=continuity_run_id,
                natural_day=last.natural_day,
                day_index=7,
                status="completed",
                expected_version=last.version,
                evaluated_at=self._timestamp(now),
            )
        if last.natural_day > today:
            return SanguoDailyContinuityScheduleV1(
                environment_id=environment_id,
                continuity_run_id=continuity_run_id,
                natural_day=last.natural_day,
                day_index=last.day_index,
                status="blocked",
                expected_version=last.version,
                reasons=["日账包含晚于可信系统日期的未来自然日。"],
                evaluated_at=self._timestamp(now),
            )
        if last.natural_day < today:
            expected_day = last.natural_day + timedelta(days=1)
            if last.state != "sealed":
                reason = "前一自然日尚未封账，不能在新自然日继续。"
            elif expected_day != today:
                reason = "连续日账已漏过至少一个自然日。"
            else:
                return SanguoDailyContinuityScheduleV1(
                    environment_id=environment_id,
                    continuity_run_id=continuity_run_id,
                    natural_day=today,
                    day_index=last.day_index + 1,
                    status="not_started",
                    next_duty=DAILY_DUTIES[0],
                    expected_version=0,
                    evaluated_at=self._timestamp(now),
                )
            return SanguoDailyContinuityScheduleV1(
                environment_id=environment_id,
                continuity_run_id=continuity_run_id,
                natural_day=today,
                day_index=min(last.day_index + 1, 7),
                status="blocked",
                expected_version=last.version,
                reasons=[reason],
                evaluated_at=self._timestamp(now),
            )
        next_duty = (
            DAILY_DUTIES[len(last.duties)]
            if len(last.duties) < len(DAILY_DUTIES)
            else None
        )
        if last.state == "interrupted":
            status = "interrupted"
        elif last.state == "sealed":
            status = "sealed"
        elif next_duty is None:
            status = "ready_to_seal"
        else:
            status = "in_progress"
        return SanguoDailyContinuityScheduleV1(
            environment_id=environment_id,
            continuity_run_id=continuity_run_id,
            natural_day=today,
            day_index=last.day_index,
            status=status,
            next_duty=next_duty,
            expected_version=last.version,
            evaluated_at=self._timestamp(now),
        )

    def list_events(
        self, environment_id: str, continuity_run_id: str, natural_day: date
    ) -> list[SanguoDailyContinuityEventV1]:
        with self.player_store._connection() as connection:
            rows = connection.execute(
                """
                SELECT body_json FROM ai_player_sanguo_daily_continuity_events
                WHERE environment_id=? AND continuity_run_id=? AND natural_day=?
                ORDER BY new_version
                """,
                (environment_id, continuity_run_id, natural_day.isoformat()),
            ).fetchall()
        return [SanguoDailyContinuityEventV1.model_validate_json(row["body_json"]) for row in rows]

    def record_duty(
        self, command: RecordSanguoDailyDutyCommand
    ) -> SanguoDailyContinuityDayV1:
        operation = "record_duty"
        request_sha256 = self._request_hash(operation, command)
        with self.player_store._connection() as connection:
            replay = self._idempotent_result(
                connection,
                command_id=command.command_id,
                request_sha256=request_sha256,
            )
        if replay is not None:
            return replay
        now = self._now(command.environment_id)
        self._assert_today(command.natural_day, now)
        self._validate_environment(command.environment_id)
        preview = self.get_day(
            command.environment_id, command.continuity_run_id, command.natural_day
        )
        used_step_ids = {
            step_id
            for duty in (preview.duties if preview else [])
            for reference in duty.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        task_snapshots = self._task_snapshots(
            command.environment_id,
            command.task_ids,
            natural_day=command.natural_day,
        )
        used_task_versions = {
            (snapshot.id, snapshot.version)
            for duty in (preview.duties if preview else [])
            for snapshot in duty.task_snapshots
        }
        if any(
            (snapshot.id, snapshot.version) in used_task_versions
            for snapshot in task_snapshots
        ):
            raise SanguoDailyContinuityError(
                "task_version_reused",
                "目标、业务推进和明日任务必须各自绑定当天新建或更新的任务版本。",
            )
        guide_freshness = self._guide_freshness(
            command.environment_id,
            command.guide_refs,
            supplied_conclusion=command.guide_freshness_conclusion,
            supplied_reason=command.guide_freshness_reason,
            now=now,
        )
        completion = SanguoDailyDutyCompletionV1(
            duty=command.duty,
            session_ids=command.session_ids,
            task_snapshots=task_snapshots,
            guide_refs=command.guide_refs,
            guide_freshness=guide_freshness,
            memory_record_ids=command.memory_record_ids,
            evidence_refs=command.evidence_refs,
            summary=command.summary,
            completed_at=self._timestamp(now),
        )
        self._validate_links(
            command.environment_id,
            natural_day=command.natural_day,
            now=now,
            session_ids=completion.session_ids,
            task_snapshots=completion.task_snapshots,
            guide_refs=completion.guide_refs,
            memory_record_ids=completion.memory_record_ids,
            evidence_refs=completion.evidence_refs,
            already_used_step_ids=used_step_ids,
        )
        timestamp = self._timestamp(now)
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection,
                command_id=command.command_id,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            current = self._get_day_in_connection(
                connection,
                command.environment_id,
                command.continuity_run_id,
                command.natural_day,
            )
            if current is None:
                duplicate = connection.execute(
                    """
                    SELECT continuity_run_id FROM ai_player_sanguo_daily_continuity_days
                    WHERE environment_id=? AND natural_day=?
                    """,
                    (command.environment_id, command.natural_day.isoformat()),
                ).fetchone()
                if duplicate:
                    raise SanguoDailyContinuityError(
                        "duplicate_natural_day", "同一环境的同一自然日只能有一份日账。"
                    )
                prior = connection.execute(
                    """
                    SELECT body_json FROM ai_player_sanguo_daily_continuity_days
                    WHERE environment_id=? AND continuity_run_id=?
                    ORDER BY day_index DESC LIMIT 1
                    """,
                    (command.environment_id, command.continuity_run_id),
                ).fetchone()
                if prior is None:
                    if command.day_index != 1:
                        raise SanguoDailyContinuityError(
                            "invalid_first_day", "新的连续经营批次必须从当天 Day 1 开始。"
                        )
                else:
                    previous = SanguoDailyContinuityDayV1.model_validate_json(
                        prior["body_json"]
                    )
                    expected_day = previous.natural_day + timedelta(days=1)
                    if previous.state != "sealed":
                        raise SanguoDailyContinuityError(
                            "previous_day_unsealed", "上一自然日尚未封账，不能开始下一日。"
                        )
                    if command.day_index != previous.day_index + 1:
                        raise SanguoDailyContinuityError(
                            "day_index_jump", "连续经营日序号不能跳号。"
                        )
                    if command.natural_day != expected_day:
                        raise SanguoDailyContinuityError(
                            "natural_day_gap", "连续经营批次不能跨过缺失的自然日。"
                        )
                if command.expected_version != 0 or command.duty != DAILY_DUTIES[0]:
                    raise SanguoDailyContinuityError(
                        "invalid_day_open",
                        "新日账必须以版本 0 写入登录后覆盖审计，禁止建立空记录。",
                    )
                updated = SanguoDailyContinuityDayV1(
                    environment_id=command.environment_id,
                    continuity_run_id=command.continuity_run_id,
                    natural_day=command.natural_day,
                    day_index=command.day_index,
                    state="in_progress",
                    version=1,
                    duties=[completion],
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                connection.execute(
                    """
                    INSERT INTO ai_player_sanguo_daily_continuity_days(
                        environment_id, continuity_run_id, natural_day, day_index,
                        state, version, body_json, created_at, updated_at, sealed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,NULL)
                    """,
                    (
                        updated.environment_id,
                        updated.continuity_run_id,
                        updated.natural_day.isoformat(),
                        updated.day_index,
                        updated.state,
                        updated.version,
                        self._json(updated),
                        updated.created_at,
                        updated.updated_at,
                    ),
                )
                previous_version = 0
            else:
                if current.version != command.expected_version:
                    raise SanguoDailyContinuityError(
                        "version_conflict", "日账已被其他执行者更新，请刷新后重试。"
                    )
                if current.day_index != command.day_index:
                    raise SanguoDailyContinuityError("day_identity_mismatch", "日账 Day 序号不匹配。")
                if current.state != "in_progress":
                    raise SanguoDailyContinuityError(
                        "day_not_writable", "日账中断时需先恢复；封账后不可追加。"
                    )
                expected_duty = DAILY_DUTIES[len(current.duties)] if len(current.duties) < 6 else None
                if command.duty != expected_duty:
                    raise SanguoDailyContinuityError(
                        "duty_order_mismatch", "六项每日职责必须按固定顺序逐项完成且不得重复。"
                    )
                updated = current.model_copy(
                    update={
                        "version": current.version + 1,
                        "duties": [*current.duties, completion],
                        "updated_at": timestamp,
                    }
                )
                changed = connection.execute(
                    """
                    UPDATE ai_player_sanguo_daily_continuity_days
                    SET state=?, version=?, body_json=?, updated_at=?
                    WHERE environment_id=? AND continuity_run_id=? AND natural_day=? AND version=?
                    """,
                    (
                        updated.state,
                        updated.version,
                        self._json(updated),
                        updated.updated_at,
                        updated.environment_id,
                        updated.continuity_run_id,
                        updated.natural_day.isoformat(),
                        current.version,
                    ),
                ).rowcount
                if changed != 1:
                    raise SanguoDailyContinuityError(
                        "version_conflict", "日账已被其他执行者更新，请刷新后重试。"
                    )
                previous_version = current.version
            event = self._build_event(
                connection,
                command=command,
                operation="record_duty",
                event_type="duty_recorded",
                request_sha256=request_sha256,
                result=updated,
                previous_version=previous_version,
                timestamp=timestamp,
                evidence_refs=completion.evidence_refs,
            )
            self._insert_event(
                connection,
                event,
                command=command,
                result=updated,
                evidence_refs=completion.evidence_refs,
            )
        self._append_anchor(event)
        return updated

    def _change_state(
        self,
        command: SanguoDailyStateCommand,
        *,
        operation: Literal["interrupt", "resume"],
    ) -> SanguoDailyContinuityDayV1:
        request_sha256 = self._request_hash(operation, command)
        with self.player_store._connection() as connection:
            replay = self._idempotent_result(
                connection, command_id=command.command_id, request_sha256=request_sha256
            )
        if replay is not None:
            return replay
        now = self._now(command.environment_id)
        self._assert_today(command.natural_day, now)
        self._validate_environment(command.environment_id)
        existing_events = self.list_events(
            command.environment_id, command.continuity_run_id, command.natural_day
        )
        used_step_ids = {
            step_id
            for event in existing_events
            for reference in event.evidence_refs
            for step_id in reference.evidence_step_ids
        }
        self._validate_links(
            command.environment_id,
            natural_day=command.natural_day,
            now=now,
            session_ids=command.session_ids,
            evidence_refs=command.evidence_refs,
            already_used_step_ids=used_step_ids,
        )
        expected_state, target_state = (
            ("in_progress", "interrupted")
            if operation == "interrupt"
            else ("interrupted", "in_progress")
        )
        event_type: DailyEventType = "interrupted" if operation == "interrupt" else "resumed"
        timestamp = self._timestamp(now)
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection, command_id=command.command_id, request_sha256=request_sha256
            )
            if replay is not None:
                return replay
            current = self._get_day_in_connection(
                connection, command.environment_id, command.continuity_run_id, command.natural_day
            )
            if current is None:
                raise SanguoDailyContinuityError("day_not_found", "找不到要更新的日账。")
            if current.version != command.expected_version:
                raise SanguoDailyContinuityError("version_conflict", "日账版本冲突。")
            if current.state != expected_state:
                raise SanguoDailyContinuityError(
                    "invalid_state_transition", f"日账当前状态不能执行 {operation}。"
                )
            updated = current.model_copy(
                update={
                    "state": target_state,
                    "version": current.version + 1,
                    "interruption_count": current.interruption_count
                    + (1 if operation == "interrupt" else 0),
                    "updated_at": timestamp,
                }
            )
            changed = connection.execute(
                """
                UPDATE ai_player_sanguo_daily_continuity_days
                SET state=?, version=?, body_json=?, updated_at=?
                WHERE environment_id=? AND continuity_run_id=? AND natural_day=? AND version=?
                """,
                (
                    updated.state,
                    updated.version,
                    self._json(updated),
                    updated.updated_at,
                    updated.environment_id,
                    updated.continuity_run_id,
                    updated.natural_day.isoformat(),
                    current.version,
                ),
            ).rowcount
            if changed != 1:
                raise SanguoDailyContinuityError("version_conflict", "日账版本冲突。")
            event = self._build_event(
                connection,
                command=command,
                operation=operation,
                event_type=event_type,
                request_sha256=request_sha256,
                result=updated,
                previous_version=current.version,
                timestamp=timestamp,
                evidence_refs=command.evidence_refs,
            )
            self._insert_event(
                connection,
                event,
                command=command,
                result=updated,
                evidence_refs=command.evidence_refs,
            )
        self._append_anchor(event)
        return updated

    def interrupt(self, command: SanguoDailyStateCommand) -> SanguoDailyContinuityDayV1:
        return self._change_state(command, operation="interrupt")

    def resume(self, command: SanguoDailyStateCommand) -> SanguoDailyContinuityDayV1:
        return self._change_state(command, operation="resume")


    def _daily_action_quality_samples(
        self,
        day: SanguoDailyContinuityDayV1,
    ) -> list[Any]:
        environment = self._validate_environment(day.environment_id)
        if environment.channel == "fixture":
            return []
        samples = [
            sample
            for sample in self.player_store.list_action_quality_samples(
                day.environment_id,
                limit=1_000_000,
            )
            if self._parse_timestamp(sample.created_at, f"动作质量样本 {sample.id} 时间")
            .astimezone(SHANGHAI)
            .date()
            == day.natural_day
        ]
        if not samples:
            raise SanguoDailyContinuityError(
                "daily_iteration_samples_missing",
                "当天没有与真实执行闭环绑定的动作质量样本，不能完成持续迭代日结。",
            )
        return sorted(samples, key=lambda item: (item.created_at, item.id))

    def _assess_daily_player_iteration(
        self,
        day: SanguoDailyContinuityDayV1,
    ) -> None:
        ordered = self._daily_action_quality_samples(day)
        if not ordered:
            return
        PlayerIterationMonitor(self.player_store).assess(
            day.environment_id,
            "daily_close",
            [item.id for item in ordered],
        )

    def seal(self, command: SealSanguoDailyContinuityCommand) -> SanguoDailyContinuityDayV1:
        operation = "seal"
        request_sha256 = self._request_hash(operation, command)
        with self.player_store._connection() as connection:
            replay = self._idempotent_result(
                connection, command_id=command.command_id, request_sha256=request_sha256
            )
        if replay is not None:
            self._assess_daily_player_iteration(replay)
            return replay
        now = self._now(command.environment_id)
        self._assert_today(command.natural_day, now)
        self._validate_environment(command.environment_id)
        preview = self.get_day(
            command.environment_id,
            command.continuity_run_id,
            command.natural_day,
        )
        if preview is None:
            raise SanguoDailyContinuityError("day_not_found", "找不到要封账的日账。")
        self._daily_action_quality_samples(preview)
        timestamp = self._timestamp(now)
        with self.player_store._write_lock, self.player_store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_result(
                connection, command_id=command.command_id, request_sha256=request_sha256
            )
            if replay is not None:
                return replay
            current = self._get_day_in_connection(
                connection, command.environment_id, command.continuity_run_id, command.natural_day
            )
            if current is None:
                raise SanguoDailyContinuityError("day_not_found", "找不到要封账的日账。")
            if current.version != command.expected_version:
                raise SanguoDailyContinuityError("version_conflict", "日账版本冲突。")
            if current.state != "in_progress" or [item.duty for item in current.duties] != list(
                DAILY_DUTIES
            ):
                raise SanguoDailyContinuityError(
                    "day_incomplete", "只有完成六项职责且已恢复的当天日账可以封账。"
                )
            updated = current.model_copy(
                update={
                    "state": "sealed",
                    "version": current.version + 1,
                    "updated_at": timestamp,
                    "sealed_at": timestamp,
                }
            )
            updated = SanguoDailyContinuityDayV1.model_validate(updated.model_dump())
            changed = connection.execute(
                """
                UPDATE ai_player_sanguo_daily_continuity_days
                SET state='sealed', version=?, body_json=?, updated_at=?, sealed_at=?
                WHERE environment_id=? AND continuity_run_id=? AND natural_day=? AND version=?
                """,
                (
                    updated.version,
                    self._json(updated),
                    updated.updated_at,
                    updated.sealed_at,
                    updated.environment_id,
                    updated.continuity_run_id,
                    updated.natural_day.isoformat(),
                    current.version,
                ),
            ).rowcount
            if changed != 1:
                raise SanguoDailyContinuityError("version_conflict", "日账版本冲突。")
            event = self._build_event(
                connection,
                command=command,
                operation="seal",
                event_type="sealed",
                request_sha256=request_sha256,
                result=updated,
                previous_version=current.version,
                timestamp=timestamp,
            )
            self._insert_event(
                connection, event, command=command, result=updated
            )
        self._append_anchor(event)
        self._assess_daily_player_iteration(updated)
        return updated

    def assess(
        self, environment_id: str, continuity_run_id: str
    ) -> SanguoDailyContinuityAssessmentV1:
        now = self._now(environment_id)
        self._validate_environment(environment_id)
        reasons: list[str] = []
        with self.player_store._connection() as connection:
            raw_day_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM ai_player_sanguo_daily_continuity_days
                    WHERE environment_id=? AND continuity_run_id=?
                    """,
                    (environment_id, continuity_run_id),
                ).fetchone()[0]
            )
        try:
            days = self.list_days(environment_id, continuity_run_id)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            return SanguoDailyContinuityAssessmentV1(
                environment_id=environment_id,
                continuity_run_id=continuity_run_id,
                recorded_natural_days=min(raw_day_count, 7),
                sealed_natural_days=0,
                continuity_component_passed=False,
                reasons=[f"日账快照无法通过机器契约：{type(exc).__name__}。"],
                evaluated_at=self._timestamp(now),
            )
        if len(days) != 7:
            reasons.append(f"需要 7 个自然日，当前只有 {len(days)} 个。")
        if [item.day_index for item in days] != list(range(1, len(days) + 1)):
            reasons.append("Day 序号不连续。")
        for previous, current in zip(days, days[1:]):
            if current.natural_day != previous.natural_day + timedelta(days=1):
                reasons.append("自然日不连续。")
                break
        current_natural_day = now.astimezone(SHANGHAI).date()
        if any(day.natural_day > current_natural_day for day in days):
            reasons.append("日账包含晚于可信系统当前日期的未来自然日。")
        command_models: dict[str, type[BaseModel]] = {
            "record_duty": RecordSanguoDailyDutyCommand,
            "interrupt": SanguoDailyStateCommand,
            "resume": SanguoDailyStateCommand,
            "seal": SealSanguoDailyContinuityCommand,
        }
        for day in days:
            if day.state != "sealed":
                reasons.append(f"Day {day.day_index} 尚未封账。")
            if [item.duty for item in day.duties] != list(DAILY_DUTIES):
                reasons.append(f"Day {day.day_index} 未完成六项每日职责。")
            if any(not item.evidence_refs for item in day.duties):
                reasons.append(f"Day {day.day_index} 存在无证据职责。")
            if not {session for item in day.duties for session in item.session_ids}:
                reasons.append(f"Day {day.day_index} 缺少会话绑定。")
            if not {
                task.id for item in day.duties for task in item.task_snapshots
            }:
                reasons.append(f"Day {day.day_index} 缺少任务绑定。")
            if not {guide.id for item in day.duties for guide in item.guide_refs}:
                reasons.append(f"Day {day.day_index} 缺少攻略知识绑定。")
            with self.player_store._connection() as connection:
                event_rows = connection.execute(
                    """
                    SELECT *
                    FROM ai_player_sanguo_daily_continuity_events
                    WHERE environment_id=? AND continuity_run_id=? AND natural_day=?
                    ORDER BY new_version, created_at, id
                    """,
                    (environment_id, continuity_run_id, day.natural_day.isoformat()),
                ).fetchall()
            events: list[SanguoDailyContinuityEventV1] = []
            results: list[SanguoDailyContinuityDayV1] = []
            commands: list[BaseModel] = []
            event_times: list[datetime] = []
            expected_previous_hash = "0" * 64
            for position, row in enumerate(event_rows, start=1):
                try:
                    event = SanguoDailyContinuityEventV1.model_validate_json(
                        row["body_json"]
                    )
                    result = SanguoDailyContinuityDayV1.model_validate_json(
                        row["result_day_json"]
                    )
                    operation = row["operation"]
                    command_model = command_models.get(operation)
                    if command_model is None or row["command_json"] is None:
                        raise ValueError("missing operation or command payload")
                    command = command_model.model_validate_json(row["command_json"])
                    event_time = self._parse_timestamp(
                        event.timestamp, f"日账事件 {event.id} 时间"
                    )
                except (
                    ValidationError,
                    ValueError,
                    json.JSONDecodeError,
                    SanguoDailyContinuityError,
                ) as exc:
                    reasons.append(
                        f"Day {day.day_index} 第 {position} 条审计事件无法解析："
                        f"{type(exc).__name__}。"
                    )
                    continue
                events.append(event)
                results.append(result)
                commands.append(command)
                event_times.append(event_time)
                if (
                    event_time > now
                    or event_time.astimezone(SHANGHAI).date() != day.natural_day
                ):
                    reasons.append(f"Day {day.day_index} 的事件时间不属于可信当天。")
                column_checks = {
                    "id": event.id,
                    "environment_id": event.environment_id,
                    "continuity_run_id": event.continuity_run_id,
                    "natural_day": event.natural_day.isoformat(),
                    "event_type": event.event_type,
                    "operation": event.operation,
                    "command_id": event.command_id,
                    "request_sha256": event.request_sha256,
                    "previous_version": event.previous_version,
                    "new_version": event.new_version,
                    "created_at": event.timestamp,
                    "previous_event_sha256": event.previous_event_sha256,
                    "event_sha256": event.event_sha256,
                }
                if any(row[key] != expected for key, expected in column_checks.items()):
                    reasons.append(f"Day {day.day_index} 的事件列与事件正文不一致。")
                if self._json(event) != row["body_json"]:
                    reasons.append(f"Day {day.day_index} 的事件正文不是 canonical JSON。")
                if self._json(command) != row["command_json"]:
                    reasons.append(f"Day {day.day_index} 的命令正文不是 canonical JSON。")
                if self._json(result) != row["result_day_json"]:
                    reasons.append(f"Day {day.day_index} 的结果正文不是 canonical JSON。")
                if event.id != self._event_id(event.command_id):
                    reasons.append(f"Day {day.day_index} 的事件 ID 与 command_id 不一致。")
                computed_request_hash = self._request_hash(event.operation, command)
                if event.request_sha256 != computed_request_hash:
                    reasons.append(f"Day {day.day_index} 的 request hash 不一致。")
                if event.previous_event_sha256 != expected_previous_hash:
                    reasons.append(f"Day {day.day_index} 的事件哈希链断裂。")
                event_without_hash = event.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude={"schema_id", "event_sha256"},
                )
                computed_event_hash = self._event_hash(
                    event_without_hash,
                    row["command_json"],
                    row["result_day_json"],
                )
                if event.event_sha256 != computed_event_hash:
                    reasons.append(f"Day {day.day_index} 的事件内容哈希不一致。")
                expected_previous_hash = event.event_sha256

            expected_anchors = [
                {
                    "event_id": event.id,
                    "new_version": event.new_version,
                    "previous_event_sha256": event.previous_event_sha256,
                    "event_sha256": event.event_sha256,
                    "anchored_at": event.timestamp,
                }
                for event in events
            ]
            try:
                anchors = self._read_anchors(
                    environment_id, continuity_run_id, day.natural_day
                )
            except (ValueError, json.JSONDecodeError, SanguoDailyContinuityError):
                anchors = []
            if anchors != expected_anchors:
                reasons.append(f"Day {day.day_index} 的数据库外锚点链不一致。")
            if [event.new_version for event in events] != list(range(1, day.version + 1)):
                reasons.append(f"Day {day.day_index} 的追加式审计版本链不完整。")
            if any(
                event.previous_version != index - 1
                for index, event in enumerate(events, start=1)
            ):
                reasons.append(f"Day {day.day_index} 的事件前置版本不连续。")
            seal_positions = [
                index for index, event in enumerate(events) if event.event_type == "sealed"
            ]
            if seal_positions != ([len(events) - 1] if events else []):
                reasons.append(f"Day {day.day_index} 必须恰好有一个末尾 seal 事件。")
            if sum(event.event_type == "duty_recorded" for event in events) != 6:
                reasons.append(f"Day {day.day_index} 的六个职责审计事件不完整。")
            interrupted = 0
            replayed: SanguoDailyContinuityDayV1 | None = None
            used_step_ids: set[str] = set()
            for event, result, command, event_time in zip(
                events, results, commands, event_times, strict=True
            ):
                if result.environment_id != environment_id or result.natural_day != day.natural_day:
                    reasons.append(f"Day {day.day_index} 的事件结果跨越了日账身份。")
                if event.actor != getattr(command, "actor", None) or event.reason != getattr(
                    command, "reason", None
                ):
                    reasons.append(f"Day {day.day_index} 的事件人读字段与命令不一致。")
                if event.operation == "record_duty":
                    assert isinstance(command, RecordSanguoDailyDutyCommand)
                    duty_position = 0 if replayed is None else len(replayed.duties)
                    expected_duty = (
                        DAILY_DUTIES[duty_position]
                        if duty_position < len(DAILY_DUTIES)
                        else None
                    )
                    if command.duty != expected_duty:
                        reasons.append(f"Day {day.day_index} 的职责事件顺序错误。")
                    if replayed is None:
                        if (
                            result.version != 1
                            or result.state != "in_progress"
                            or result.created_at != event.timestamp
                            or result.updated_at != event.timestamp
                            or len(result.duties) != 1
                        ):
                            reasons.append(f"Day {day.day_index} 的首个职责结果语义错误。")
                    else:
                        expected_result = replayed.model_copy(
                            update={
                                "version": replayed.version + 1,
                                "duties": [*replayed.duties, result.duties[-1]],
                                "updated_at": event.timestamp,
                            }
                        )
                        if result != expected_result:
                            reasons.append(f"Day {day.day_index} 的职责结果不是单步增量。")
                    completion = result.duties[-1]
                    if (
                        completion.duty != command.duty
                        or completion.session_ids != command.session_ids
                        or completion.guide_refs != command.guide_refs
                        or completion.memory_record_ids != command.memory_record_ids
                        or completion.evidence_refs != command.evidence_refs
                        or completion.summary != command.summary
                        or completion.completed_at != event.timestamp
                        or {item.id for item in completion.task_snapshots}
                        != set(command.task_ids)
                    ):
                        reasons.append(f"Day {day.day_index} 的职责结果与命令不一致。")
                    if event.evidence_refs != completion.evidence_refs:
                        reasons.append(f"Day {day.day_index} 的职责事件证据与结果不一致。")
                    if completion.guide_freshness is not None:
                        try:
                            expected_freshness = self._guide_freshness(
                                environment_id,
                                completion.guide_refs,
                                supplied_conclusion=completion.guide_freshness.conclusion,
                                supplied_reason=completion.guide_freshness.reason,
                                now=event_time,
                            )
                            if expected_freshness != completion.guide_freshness:
                                reasons.append(
                                    f"Day {day.day_index} 的结构化攻略新鲜度结论不一致。"
                                )
                        except SanguoDailyContinuityError as exc:
                            reasons.append(
                                f"Day {day.day_index} 的攻略新鲜度失效：{exc.code}。"
                            )
                elif event.operation == "interrupt":
                    if replayed is None or replayed.state != "in_progress":
                        reasons.append(f"Day {day.day_index} 的中断没有合法前态。")
                    else:
                        interrupted += 1
                        expected_result = replayed.model_copy(
                            update={
                                "state": "interrupted",
                                "version": replayed.version + 1,
                                "interruption_count": interrupted,
                                "updated_at": event.timestamp,
                            }
                        )
                        if result != expected_result:
                            reasons.append(f"Day {day.day_index} 的中断结果语义错误。")
                elif event.operation == "resume":
                    if replayed is None or replayed.state != "interrupted":
                        reasons.append(f"Day {day.day_index} 的恢复没有配对中断。")
                    else:
                        expected_result = replayed.model_copy(
                            update={
                                "state": "in_progress",
                                "version": replayed.version + 1,
                                "updated_at": event.timestamp,
                            }
                        )
                        if result != expected_result:
                            reasons.append(f"Day {day.day_index} 的恢复结果语义错误。")
                elif event.operation == "seal":
                    if (
                        replayed is None
                        or replayed.state != "in_progress"
                        or len(replayed.duties) != 6
                    ):
                        reasons.append(f"Day {day.day_index} 的 seal 没有完整合法前态。")
                    else:
                        expected_result = replayed.model_copy(
                            update={
                                "state": "sealed",
                                "version": replayed.version + 1,
                                "updated_at": event.timestamp,
                                "sealed_at": event.timestamp,
                            }
                        )
                        if result != expected_result:
                            reasons.append(f"Day {day.day_index} 的 seal 结果语义错误。")
                if event.operation != "seal":
                    try:
                        self._validate_links(
                            environment_id,
                            natural_day=day.natural_day,
                            now=event_time,
                            session_ids=list(getattr(command, "session_ids", [])),
                            task_snapshots=(
                                result.duties[-1].task_snapshots
                                if event.operation == "record_duty"
                                else ()
                            ),
                            guide_refs=(
                                result.duties[-1].guide_refs
                                if event.operation == "record_duty"
                                else ()
                            ),
                            memory_record_ids=(
                                result.duties[-1].memory_record_ids
                                if event.operation == "record_duty"
                                else ()
                            ),
                            evidence_refs=event.evidence_refs,
                            already_used_step_ids=used_step_ids,
                        )
                    except SanguoDailyContinuityError as exc:
                        reasons.append(
                            f"Day {day.day_index} 的 {event.event_type} 引用失效：{exc.code}。"
                        )
                    used_step_ids.update(
                        step_id
                        for reference in event.evidence_refs
                        for step_id in reference.evidence_step_ids
                    )
                replayed = result
            if replayed != day:
                reasons.append(f"Day {day.day_index} 快照不是事件重放的最终派生结果。")
            if interrupted != day.interruption_count:
                reasons.append(f"Day {day.day_index} 的中断计数与事件重放不一致。")
        reasons = list(dict.fromkeys(reasons))
        passed = len(days) == 7 and not reasons
        return SanguoDailyContinuityAssessmentV1(
            environment_id=environment_id,
            continuity_run_id=continuity_run_id,
            recorded_natural_days=len(days),
            sealed_natural_days=sum(day.state == "sealed" for day in days),
            continuity_component_passed=passed,
            reasons=reasons,
            evaluated_at=self._timestamp(now),
        )


__all__ = [
    "DAILY_DUTIES",
    "DailyGuideReferenceV1",
    "DailyGuideFreshnessV1",
    "DailyTaskSnapshotV1",
    "DailyTerminalEvidenceReferenceV1",
    "RecordSanguoDailyDutyCommand",
    "SanguoDailyContinuityAssessmentV1",
    "SanguoDailyContinuityDayV1",
    "SanguoDailyContinuityError",
    "SanguoDailyContinuityEventV1",
    "SanguoDailyContinuityLedger",
    "SanguoDailyContinuityScheduleV1",
    "SanguoDailyDutyCompletionV1",
    "SanguoDailyStateCommand",
    "SealSanguoDailyContinuityCommand",
]
