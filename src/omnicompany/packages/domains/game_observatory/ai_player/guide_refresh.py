"""Durable, trigger-scoped refresh queue for time-sensitive game guides.

The A2 player only appends a request.  A separate research worker consumes the
request through the existing ``research.run`` pipeline and must return the
strict bundle below before any guide may become actionable.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from ..models import SourceSnapshot, utc_now
from .contracts import (
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    GuideKnowledgeV1,
)


GuideRefreshTrigger = Literal[
    "first_entry_to_new_system",
    "high_value_hard_to_reverse_choice",
    "version_or_season_change",
    "two_consecutive_failures",
]
GuideRefreshTerminalStatus = Literal[
    "completed",
    "offline",
    "source_unavailable",
    "failed",
]

_TRIGGER_MARKER = re.compile(
    r"\[guide-refresh:(first_entry_to_new_system|high_value_hard_to_reverse_choice|"
    r"version_or_season_change|two_consecutive_failures)\]"
)


def _timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


class GuideRefreshEnvironmentV1(BaseModel):
    """Environment identity captured when the trigger occurs."""

    model_config = ConfigDict(extra="forbid")

    environment_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    build_scope_id: str = Field(min_length=1)
    account_scope_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    game_version: str | None = Field(default=None, min_length=1)
    season: str | None = Field(default=None, min_length=1)
    server_stage: str | None = Field(default=None, min_length=1)


class GuideRefreshRequestV1(BaseModel):
    """Append-only request emitted by A2 without waiting for research."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.guide-refresh-request.v1"] = Field(
        default="game-observatory.ai-player.guide-refresh-request.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trigger: GuideRefreshTrigger
    query: str = Field(min_length=3, max_length=1000)
    environment: GuideRefreshEnvironmentV1
    current_state_id: str | None = Field(default=None, min_length=1)
    consecutive_failures: int = Field(default=0, ge=0)
    attempt: int = Field(default=1, ge=1)
    retry_of_request_id: str | None = Field(default=None, min_length=1)
    evidence_refs: list[EvidenceReferenceV1] = Field(min_length=1)
    status: Literal["queued"] = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.fromisoformat(utc_now()))

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("guide refresh query is blank")
        return normalized

    @field_validator("created_at")
    @classmethod
    def require_created_timezone(cls, value: datetime) -> datetime:
        return _timezone_aware(value, "guide refresh created_at")

    @model_validator(mode="after")
    def keep_trigger_context_canonical(self) -> "GuideRefreshRequestV1":
        if self.environment.environment_id != self.environment_id:
            raise ValueError("guide refresh environment snapshot does not match request")
        if any(ref.environment_id != self.environment_id for ref in self.evidence_refs):
            raise ValueError("guide refresh evidence must belong to the request environment")
        if self.trigger == "two_consecutive_failures":
            if self.consecutive_failures < 2:
                raise ValueError("two_consecutive_failures requires at least two failures")
        elif self.consecutive_failures:
            raise ValueError("failure count is only valid for the failure trigger")
        if self.attempt == 1 and self.retry_of_request_id is not None:
            raise ValueError("first guide refresh attempt cannot cite a retry parent")
        if self.attempt > 1 and self.retry_of_request_id is None:
            raise ValueError("guide refresh retry requires its previous request id")
        return self


class GuideSourceMetadataV1(BaseModel):
    """Required provenance stored inside every source snapshot."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    platform: str = Field(min_length=1)
    author_or_publisher: str = Field(min_length=1)
    publisher: str | None = Field(default=None, min_length=1)
    author: str | None = Field(default=None, min_length=1)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    retrieved_at: datetime
    game_version: str | None = Field(default=None, min_length=1)
    season: str | None = Field(default=None, min_length=1)
    server_stage: str | None = Field(default=None, min_length=1)
    citation_locator: str = Field(min_length=1)
    research_record_id: str = Field(min_length=1)

    @field_validator("published_at", "updated_at", "retrieved_at")
    @classmethod
    def require_source_timezones(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _timezone_aware(value, f"guide source {info.field_name}")

    @model_validator(mode="after")
    def require_publication_or_update_time(self) -> "GuideSourceMetadataV1":
        if self.published_at is None and self.updated_at is None:
            raise ValueError("guide source requires a publication or update time")
        if self.publisher is None:
            self.publisher = self.author_or_publisher
        return self


class GuideSourceSnapshotV1(SourceSnapshot):
    """A normal SourceSnapshot with fail-closed guide provenance metadata."""

    metadata: GuideSourceMetadataV1

    @field_validator("captured_at")
    @classmethod
    def require_captured_timezone(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("source snapshot captured_at must use ISO-8601") from error
        _timezone_aware(parsed, "source snapshot captured_at")
        return value

    @model_validator(mode="after")
    def keep_locator_and_capture_time_consistent(self) -> "GuideSourceSnapshotV1":
        if self.locator != self.metadata.citation_locator:
            raise ValueError("source snapshot locator must equal the citation locator")
        captured = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        if captured != self.metadata.retrieved_at:
            raise ValueError("source snapshot capture time must equal retrieved_at")
        return self


class GuideResearchResultBundleV1(BaseModel):
    """Strict handoff from the independent research worker to game storage."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.guide-research-result.v1"] = Field(
        default="game-observatory.ai-player.guide-research-result.v1",
        alias="schema",
    )
    request_id: str = Field(min_length=1)
    research_pipeline: Literal["research.run"] = "research.run"
    research_record_id: str = Field(min_length=1)
    source_snapshots: list[GuideSourceSnapshotV1] = Field(min_length=1)
    guides: list[GuideKnowledgeV1] = Field(min_length=1)
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def require_completed_timezone(cls, value: datetime) -> datetime:
        return _timezone_aware(value, "guide research completed_at")

    @model_validator(mode="after")
    def bind_guides_to_exact_sources(self) -> "GuideResearchResultBundleV1":
        if len({item.id for item in self.source_snapshots}) != len(self.source_snapshots):
            raise ValueError("source snapshot ids must be unique")
        if len({item.source_id for item in self.source_snapshots}) != len(
            self.source_snapshots
        ):
            raise ValueError("guide source ids must be unique in one result")
        guide_keys = {(item.id, item.version) for item in self.guides}
        if len(guide_keys) != len(self.guides):
            raise ValueError("guide ids and versions must be unique")

        sources = {item.source_id: item for item in self.source_snapshots}
        for snapshot in self.source_snapshots:
            if snapshot.metadata.research_record_id != self.research_record_id:
                raise ValueError("source snapshot belongs to a different research record")
        for guide in self.guides:
            source_ids = {
                source_id
                for ref in guide.evidence_refs
                for source_id in ref.source_ids
            }
            if len(source_ids) != 1 or not source_ids.issubset(sources):
                raise ValueError("each guide must cite exactly one bundled source")
            source = sources[next(iter(source_ids))]
            metadata = source.metadata
            if str(guide.url) != str(metadata.url):
                raise ValueError("guide URL does not match its source snapshot")
            expected_author = metadata.author or metadata.publisher or metadata.author_or_publisher
            if guide.platform != metadata.platform or guide.author != expected_author:
                raise ValueError("guide platform or author does not match its source snapshot")
            if guide.retrieved_at != metadata.retrieved_at.isoformat():
                raise ValueError("guide retrieval time does not match its source snapshot")
            publication = metadata.published_at.isoformat() if metadata.published_at else None
            updated = metadata.updated_at.isoformat() if metadata.updated_at else None
            if guide.published_at != publication or guide.updated_at != updated:
                raise ValueError("guide publication times do not match its source snapshot")
            if guide.locators != [metadata.citation_locator]:
                raise ValueError("guide citation locator does not match its source snapshot")
            source_applicability = (
                metadata.game_version,
                metadata.season,
                metadata.server_stage,
            )
            guide_applicability = (
                guide.applicable_game_version,
                guide.season,
                guide.server_stage,
            )
            if guide_applicability != source_applicability:
                raise ValueError("guide applicability does not match its source snapshot")
            if any(value is None for value in source_applicability) and (
                guide.status != "unverified" or not guide.missing_applicability_reason
            ):
                raise ValueError("missing source applicability must remain discovery-only")
        return self


class GuideVersionReferenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: int = Field(ge=1)


class GuideRefreshReceiptV1(BaseModel):
    """Exactly one terminal receipt for a queued refresh request."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.guide-refresh-receipt.v1"] = Field(
        default="game-observatory.ai-player.guide-refresh-receipt.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    status: GuideRefreshTerminalStatus
    research_pipeline: Literal["research.run"] = "research.run"
    research_record_id: str | None = Field(default=None, min_length=1)
    source_snapshot_ids: list[str] = Field(default_factory=list)
    guides: list[GuideVersionReferenceV1] = Field(default_factory=list)
    detail: str = Field(min_length=1, max_length=4000)
    finished_at: datetime

    @field_validator("finished_at")
    @classmethod
    def require_finished_timezone(cls, value: datetime) -> datetime:
        return _timezone_aware(value, "guide refresh finished_at")

    @model_validator(mode="after")
    def keep_terminal_claim_fail_closed(self) -> "GuideRefreshReceiptV1":
        if self.status == "completed":
            if not self.research_record_id or not self.source_snapshot_ids or not self.guides:
                raise ValueError("completed refresh requires research, sources, and guides")
        elif self.research_record_id or self.source_snapshot_ids or self.guides:
            raise ValueError("non-completed refresh cannot claim produced guide knowledge")
        if len(set(self.source_snapshot_ids)) != len(self.source_snapshot_ids):
            raise ValueError("receipt source snapshot ids must be unique")
        if len({(item.id, item.version) for item in self.guides}) != len(self.guides):
            raise ValueError("receipt guide references must be unique")
        return self


class GuideRefreshWorkItemV1(BaseModel):
    """Consumption contract handed to a research worker; it performs no polling."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.guide-refresh-work-item.v1"] = Field(
        default="game-observatory.ai-player.guide-refresh-work-item.v1",
        alias="schema",
    )
    request: GuideRefreshRequestV1
    research_pipeline: Literal["research.run"] = "research.run"
    research_topic: str = Field(min_length=1)
    required_output_schema: Literal[
        "game-observatory.ai-player.guide-research-result.v1"
    ] = "game-observatory.ai-player.guide-research-result.v1"
    completion_rule: str = Field(min_length=1)


class GuideRefreshFocusHintV1(BaseModel):
    """Compact command exposed to A2 only while a trigger is active."""

    model_config = ConfigDict(extra="forbid")

    trigger: GuideRefreshTrigger
    query: str = Field(min_length=3)
    command: str = Field(min_length=1)


def stable_refresh_request_id(
    *,
    environment_id: str,
    task_id: str,
    trigger: GuideRefreshTrigger,
    query: str,
    evidence_step_ids: list[str],
) -> str:
    payload = json.dumps(
        {
            "environment_id": environment_id,
            "task_id": task_id,
            "trigger": trigger,
            "query": " ".join(query.split()),
            "evidence_step_ids": sorted(evidence_step_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"guide-refresh.{digest}"


def build_refresh_request(
    *,
    environment: EnvironmentScopeV1,
    task: FrontierTaskV1,
    trigger: GuideRefreshTrigger,
    query: str,
    evidence_refs: list[EvidenceReferenceV1],
    game_version: str | None = None,
    season: str | None = None,
    server_stage: str | None = None,
    current_state_id: str | None = None,
    consecutive_failures: int = 0,
    created_at: str | None = None,
) -> GuideRefreshRequestV1:
    if task.environment_id != environment.id:
        raise ValueError("guide refresh task belongs to another environment")
    step_ids = [step for ref in evidence_refs for step in ref.evidence_step_ids]
    if not step_ids:
        raise ValueError("guide refresh requires at least one concrete evidence step")
    return GuideRefreshRequestV1(
        id=stable_refresh_request_id(
            environment_id=environment.id,
            task_id=task.id,
            trigger=trigger,
            query=query,
            evidence_step_ids=step_ids,
        ),
        environment_id=environment.id,
        task_id=task.id,
        trigger=trigger,
        query=query,
        environment=GuideRefreshEnvironmentV1(
            environment_id=environment.id,
            game_id=environment.game_id,
            build_scope_id=environment.build_scope_id,
            account_scope_id=environment.account_scope_id,
            channel=environment.channel,
            game_version=game_version,
            season=season,
            server_stage=server_stage,
        ),
        current_state_id=current_state_id,
        consecutive_failures=consecutive_failures,
        evidence_refs=evidence_refs,
        created_at=created_at or utc_now(),
    )


def build_refresh_work_item(request: GuideRefreshRequestV1) -> GuideRefreshWorkItemV1:
    environment = request.environment
    topic = (
        f"{environment.game_id} 时效攻略：{request.query}；"
        f"触发={request.trigger}；build={environment.build_scope_id}；"
        f"channel={environment.channel}；version={environment.game_version or 'unknown'}；"
        f"season={environment.season or 'unknown'}；"
        f"server_stage={environment.server_stage or 'unknown'}。"
        "只使用可回读的公开来源，逐条保留作者或发布主体、发布日期或更新时间、"
        "抓取时间和精确引用位置；未知适用性必须留空。"
    )
    return GuideRefreshWorkItemV1(
        request=request,
        research_topic=topic,
        completion_rule=(
            "使用既有 research.run 完成联网研究；随后按 required_output_schema 生成 "
            "SourceSnapshot 与 GuideKnowledge。不得从摘要猜测作者、时间或适用版本；"
            "来源不可用、离线或失败时提交对应终态回执。"
        ),
    )


def build_retry_request(
    request: GuideRefreshRequestV1,
    receipt: GuideRefreshReceiptV1,
    *,
    created_at: str | None = None,
) -> GuideRefreshRequestV1:
    """Create a new queued attempt without mutating the terminal predecessor."""

    if receipt.request_id != request.id or receipt.environment_id != request.environment_id:
        raise ValueError("guide refresh retry receipt does not match its request")
    if receipt.status == "completed":
        raise ValueError("completed guide refresh request cannot be retried")
    next_attempt = request.attempt + 1
    return request.model_copy(
        update={
            "id": f"{request.id}.retry.{next_attempt}",
            "attempt": next_attempt,
            "retry_of_request_id": request.id,
            "created_at": datetime.fromisoformat(created_at or utc_now()),
        }
    )
    return GuideRefreshWorkItemV1(
        request=request,
        research_topic=topic,
        completion_rule=(
            "使用既有 research.run 完成联网研究；随后按 required_output_schema 生成 "
            "SourceSnapshot 与 GuideKnowledge。不得从摘要猜测作者、时间或适用版本；"
            "来源不可用、离线或失败时提交对应终态回执。"
        ),
    )


def triggered_refresh_focus_hint(
    *,
    task: FrontierTaskV1,
    source_step_id: str,
    current_state_id: str | None,
    already_requested_task_ids: set[str] | None = None,
) -> GuideRefreshFocusHintV1 | None:
    """Return one A2 submit command only for an objectively active trigger.

    ``new_unlock`` and two recorded task failures are structured signals.  The
    other two triggers require an explicit marker in the task reason/title so a
    generic risky or stale task cannot accidentally cause routine web search.
    """

    if task.id in (already_requested_task_ids or set()):
        return None
    marker = _TRIGGER_MARKER.search(f"{task.title}\n{task.reason}")
    trigger: GuideRefreshTrigger | None = None
    if task.source == "new_unlock":
        trigger = "first_entry_to_new_system"
    elif task.attempt_count >= 2:
        trigger = "two_consecutive_failures"
    elif marker is not None:
        trigger = cast(GuideRefreshTrigger, marker.group(1))
    if trigger is None:
        return None

    query = " ".join(f"{task.title} {task.reason}".split())[:500]
    command = (
        "guide refresh submit "
        f"--environment {task.environment_id} --task {task.id} "
        f"--trigger {trigger} --query {json.dumps(query, ensure_ascii=False)} "
        f"--source-step {source_step_id}"
    )
    if current_state_id:
        command += f" --current-state {current_state_id}"
    if trigger == "two_consecutive_failures":
        command += f" --consecutive-failures {task.attempt_count}"
    return GuideRefreshFocusHintV1(trigger=trigger, query=query, command=command)


def contradicted_successor(
    guide: GuideKnowledgeV1,
    *,
    summary: str,
    evidence_refs: list[EvidenceReferenceV1],
    created_at: str | None = None,
) -> GuideKnowledgeV1:
    if not evidence_refs:
        raise ValueError("guide contradiction requires live counterexample evidence")
    if any(ref.environment_id != guide.environment_id for ref in evidence_refs):
        raise ValueError("guide contradiction evidence belongs to another environment")
    return guide.model_copy(
        update={
            "version": guide.version + 1,
            "status": "contradicted",
            "fresh_until": None,
            "contradiction_summary": summary,
            "live_contradiction_evidence_refs": evidence_refs,
            "created_at": created_at or utc_now(),
        }
    )


__all__ = [
    "GuideRefreshEnvironmentV1",
    "GuideRefreshFocusHintV1",
    "GuideRefreshReceiptV1",
    "GuideRefreshRequestV1",
    "GuideRefreshTerminalStatus",
    "GuideRefreshTrigger",
    "GuideRefreshWorkItemV1",
    "GuideResearchResultBundleV1",
    "GuideSourceMetadataV1",
    "GuideSourceSnapshotV1",
    "GuideVersionReferenceV1",
    "build_refresh_request",
    "build_retry_request",
    "build_refresh_work_item",
    "contradicted_successor",
    "stable_refresh_request_id",
    "triggered_refresh_focus_hint",
]
