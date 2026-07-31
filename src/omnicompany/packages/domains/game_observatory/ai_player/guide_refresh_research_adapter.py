"""Fail-closed asynchronous adapter between ``research.run`` and guide refresh.

The generic research pipeline currently returns a compact source list.  This
module freezes the richer, run-local handoff required by G-10 without teaching
the game domain to crawl pages or infer missing provenance.  A research runner
may be attached later; only source artifacts satisfying this contract can
become guide knowledge.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from .contracts import EvidenceReferenceV1, GuideKnowledgeV1
from .guide_refresh import (
    GuideRefreshReceiptV1,
    GuideRefreshWorkItemV1,
    GuideResearchResultBundleV1,
    GuideSourceMetadataV1,
    GuideSourceSnapshotV1,
    GuideVersionReferenceV1,
)


ResearchRunTerminalStatus = Literal[
    "completed",
    "offline",
    "source_unavailable",
    "failed",
]


class ResearchRunOfflineError(RuntimeError):
    """The upstream runner explicitly identified a network-offline outcome."""


class ResearchRunSourceUnavailableError(RuntimeError):
    """The upstream runner could not retrieve any usable public source."""


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}.{digest}"


class ResearchRunGuideSourceV1(BaseModel):
    """Run-local public source artifact.

    Fields are optional at the intake boundary so a real incomplete
    ``research.run`` result can be classified instead of crashing.  Eligibility
    is checked separately and is deliberately stricter.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1)
    url: HttpUrl | None = None
    snippet: str | None = Field(default=None, min_length=1)
    platform: str | None = Field(default=None, min_length=1)
    author_or_publisher: str | None = Field(default=None, min_length=1)
    publisher: str | None = Field(default=None, min_length=1)
    author: str | None = Field(default=None, min_length=1)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    retrieved_at: datetime | None = None
    game_version: str | None = Field(default=None, min_length=1)
    season: str | None = Field(default=None, min_length=1)
    server_stage: str | None = Field(default=None, min_length=1)
    citation_locator: str | None = Field(default=None, min_length=1)
    snapshot_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    summary: str | None = Field(default=None, min_length=1, max_length=10_000)
    fresh_until: datetime | None = None

    @field_validator("published_at", "updated_at", "retrieved_at", "fresh_until")
    @classmethod
    def require_timezones(cls, value: datetime | None, info: Any) -> datetime | None:
        return None if value is None else _aware(value, f"research source {info.field_name}")

    @model_validator(mode="after")
    def verify_detached_snapshot_hash(self) -> "ResearchRunGuideSourceV1":
        if self.publisher is None:
            self.publisher = self.author_or_publisher
        if self.author_or_publisher is None:
            self.author_or_publisher = self.publisher or self.author
        if self.snapshot_text is not None and self.snapshot_sha256 is not None:
            actual = hashlib.sha256(self.snapshot_text.encode("utf-8")).hexdigest()
            if actual != self.snapshot_sha256:
                raise ValueError("research source snapshot hash does not match its text")
        return self


class ResearchRunGuideResultV1(BaseModel):
    """Explicit result returned by an independent ``research.run`` runner."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.research-run-guide-result.v1"] = Field(
        default="game-observatory.ai-player.research-run-guide-result.v1",
        alias="schema",
    )
    request_id: str = Field(min_length=1)
    status: ResearchRunTerminalStatus
    research_pipeline: Literal["research.run"] = "research.run"
    research_record_id: str | None = Field(default=None, min_length=1)
    sources: list[ResearchRunGuideSourceV1] = Field(default_factory=list)
    detail: str = Field(min_length=1, max_length=4000)
    finished_at: datetime

    @field_validator("finished_at")
    @classmethod
    def require_finished_timezone(cls, value: datetime) -> datetime:
        return _aware(value, "research result finished_at")

    @model_validator(mode="after")
    def keep_terminal_claims_fail_closed(self) -> "ResearchRunGuideResultV1":
        if self.status == "completed":
            if self.research_record_id is None or not self.sources:
                raise ValueError("completed research requires a record and at least one source")
        elif self.research_record_id is not None or self.sources:
            raise ValueError("non-completed research cannot claim sources or a research record")
        return self


def _read_run_json(run_dir: Path, name: str) -> dict[str, Any]:
    path = run_dir / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"research.run artifact is unreadable: {name}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"research.run artifact must be an object: {name}")
    return payload


def research_run_guide_result_from_pipeline_output(
    work_item: GuideRefreshWorkItemV1,
    pipeline_output: dict[str, Any],
) -> ResearchRunGuideResultV1:
    """Bind a successful generic ``research.run`` sink output to one request.

    The sink deliberately returns only the unified record id and run directory.
    Rich source evidence remains in that run's ``native.json``.  This adapter
    consumes those existing artifacts and never fills a missing page fact.
    """

    record_id = str(pipeline_output.get("record_id") or "").strip()
    run_dir_text = str(pipeline_output.get("run_dir") or "").strip()
    if not record_id or not run_dir_text:
        raise ValueError("research.run sink output requires record_id and run_dir")
    run_dir = Path(run_dir_text)
    intake = _read_run_json(run_dir, "intake.json")
    if intake.get("topic") != work_item.research_topic:
        raise ValueError("research.run topic does not match the guide refresh work item")
    status = _read_run_json(run_dir, "native_status.json")
    if status.get("state") != "finished" or str(status.get("result_status") or "").lower() not in {
        "completed",
        "succeeded",
        "success",
    }:
        raise ValueError("research.run native worker did not finish successfully")
    native = _read_run_json(run_dir, "native.json")
    raw_sources = native.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ResearchRunSourceUnavailableError("native.json contains no public source")
    sources: list[dict[str, Any]] = []
    for index, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict):
            raise ValueError(f"native.json source[{index}] must be an object")
        source = {
            key: (value.strip() or None) if isinstance(value, str) else value
            for key, value in raw_source.items()
        }
        if "summary" not in source and "source_summary" in source:
            source["summary"] = source.pop("source_summary")
        sources.append(source)
    return ResearchRunGuideResultV1(
        request_id=work_item.request.id,
        status="completed",
        research_record_id=record_id,
        sources=sources,
        detail="research.run completed and run-local source artifacts were consumed",
        finished_at=status.get("finished_at"),
    )


class GuideRefreshResearchWorkerOutputV1(BaseModel):
    """One strict bundle or one explicit non-completed receipt."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.guide-refresh-worker-output.v1"] = Field(
        default="game-observatory.ai-player.guide-refresh-worker-output.v1",
        alias="schema",
    )
    request_id: str = Field(min_length=1)
    bundle: GuideResearchResultBundleV1 | None = None
    receipt: GuideRefreshReceiptV1 | None = None

    @model_validator(mode="after")
    def require_exactly_one_terminal_output(self) -> "GuideRefreshResearchWorkerOutputV1":
        if (self.bundle is None) == (self.receipt is None):
            raise ValueError("guide refresh worker must return exactly one bundle or receipt")
        if self.bundle is not None and self.bundle.request_id != self.request_id:
            raise ValueError("guide refresh bundle belongs to another request")
        if self.receipt is not None and self.receipt.request_id != self.request_id:
            raise ValueError("guide refresh receipt belongs to another request")
        return self


class GuideRefreshConsumeResultV1(BaseModel):
    """Receipt returned after the caller-provided durable store commits."""

    model_config = ConfigDict(extra="forbid")

    receipt: GuideRefreshReceiptV1
    inserted: dict[str, int] = Field(default_factory=dict)


class GuideRefreshResearchStore(Protocol):
    def complete_guide_refresh_request(
        self,
        bundle: GuideResearchResultBundleV1,
    ) -> tuple[GuideRefreshReceiptV1, dict[str, int]]: ...

    def append_guide_refresh_receipt(
        self,
        receipt: GuideRefreshReceiptV1,
    ) -> GuideRefreshReceiptV1: ...


ResearchRunExecutor = Callable[
    [GuideRefreshWorkItemV1],
    Awaitable[ResearchRunGuideResultV1 | dict[str, Any]],
]


def _failure_receipt(
    work_item: GuideRefreshWorkItemV1,
    *,
    status: Literal["offline", "source_unavailable", "failed"],
    detail: str,
    finished_at: datetime,
) -> GuideRefreshReceiptV1:
    request = work_item.request
    return GuideRefreshReceiptV1(
        id=f"guide-refresh-receipt.{request.id}",
        environment_id=request.environment_id,
        request_id=request.id,
        status=status,
        detail=detail[:4000],
        finished_at=finished_at,
    )


def _missing_core_fields(source: ResearchRunGuideSourceV1) -> list[str]:
    missing: list[str] = []
    for field_name in (
        "url",
        "platform",
        "publisher",
        "retrieved_at",
        "citation_locator",
        "snapshot_text",
        "snapshot_sha256",
        "summary",
    ):
        if getattr(source, field_name) is None:
            missing.append(field_name)
    if source.published_at is None and source.updated_at is None:
        missing.append("published_at_or_updated_at")
    return missing


def _applicability_gap(
    source: ResearchRunGuideSourceV1,
    work_item: GuideRefreshWorkItemV1,
) -> list[str]:
    request_environment = work_item.request.environment
    missing: list[str] = []
    for field_name in ("game_version", "season", "server_stage"):
        source_value = getattr(source, field_name)
        request_value = getattr(request_environment, field_name)
        if source_value is None:
            missing.append(field_name)
        elif request_value is None:
            missing.append(f"current_{field_name}")
        elif source_value != request_value:
            missing.append(f"conflicting_{field_name}")
    if source.fresh_until is None:
        missing.append("fresh_until")
    return missing


def _build_bundle(
    work_item: GuideRefreshWorkItemV1,
    result: ResearchRunGuideResultV1,
    eligible_sources: list[ResearchRunGuideSourceV1],
) -> GuideResearchResultBundleV1:
    request = work_item.request
    snapshots: list[GuideSourceSnapshotV1] = []
    guides: list[GuideKnowledgeV1] = []
    for source in eligible_sources:
        # All fields below were checked by _missing_core_fields.
        assert source.url is not None
        assert source.platform is not None
        assert source.publisher is not None
        assert source.author_or_publisher is not None
        assert source.retrieved_at is not None
        assert source.citation_locator is not None
        assert source.snapshot_text is not None
        assert source.snapshot_sha256 is not None
        assert source.summary is not None
        publication = source.published_at.isoformat() if source.published_at else None
        update = source.updated_at.isoformat() if source.updated_at else None
        source_id = _stable_id(
            "research-source",
            {
                "record": result.research_record_id,
                "url": str(source.url),
                "locator": source.citation_locator,
                "content_sha256": source.snapshot_sha256,
            },
        )
        metadata = GuideSourceMetadataV1(
            url=source.url,
            platform=source.platform,
            author_or_publisher=source.author_or_publisher,
            publisher=source.publisher,
            author=source.author,
            published_at=source.published_at,
            updated_at=source.updated_at,
            retrieved_at=source.retrieved_at,
            game_version=source.game_version,
            season=source.season,
            server_stage=source.server_stage,
            citation_locator=source.citation_locator,
            research_record_id=result.research_record_id,
        )
        snapshot = GuideSourceSnapshotV1(
            id=_stable_id(
                "guide-source-snapshot",
                {"source_id": source_id, "content_sha256": source.snapshot_sha256},
            ),
            source_id=source_id,
            content_sha256=source.snapshot_sha256,
            locator=source.citation_locator,
            excerpt=source.snapshot_text,
            captured_at=source.retrieved_at.isoformat(),
            metadata=metadata,
        )
        gaps = _applicability_gap(source, work_item)
        actionable = not gaps
        guide = GuideKnowledgeV1(
            id=_stable_id(
                "guide-refresh",
                {
                    "environment_id": request.environment_id,
                    "source_id": source_id,
                    "content_sha256": source.snapshot_sha256,
                },
            ),
            environment_id=request.environment_id,
            evidence_refs=[
                EvidenceReferenceV1(
                    environment_id=request.environment_id,
                    source_ids=[source_id],
                    note="攻略公开来源原文快照。",
                )
            ],
            url=source.url,
            platform=source.platform,
            author=source.author or source.publisher,
            published_at=publication,
            updated_at=update,
            retrieved_at=source.retrieved_at.isoformat(),
            fresh_until=source.fresh_until if actionable else None,
            applicable_build_scope_id=(
                request.environment.build_scope_id if actionable else None
            ),
            applicable_account_scope_id=(
                request.environment.account_scope_id if actionable else None
            ),
            applicable_channel=request.environment.channel if actionable else None,
            applicable_game_version=source.game_version,
            season=source.season,
            server_stage=source.server_stage,
            summary=source.summary,
            locators=[source.citation_locator],
            status="current" if actionable else "unverified",
            missing_applicability_reason=(
                None
                if actionable
                else "来源或当前实机环境缺少可执行适用性：" + "、".join(gaps)
            ),
            triggering_task_ids=[request.task_id],
            created_at=result.finished_at.isoformat(),
        )
        snapshots.append(snapshot)
        guides.append(guide)
    return GuideResearchResultBundleV1(
        request_id=request.id,
        research_record_id=result.research_record_id,
        source_snapshots=snapshots,
        guides=guides,
        completed_at=result.finished_at,
    )


class GuideRefreshResearchWorker:
    """Consume one work item through an injected ``research.run`` executor."""

    def __init__(
        self,
        executor: ResearchRunExecutor,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._executor = executor
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def consume(
        self,
        work_item: GuideRefreshWorkItemV1,
    ) -> GuideRefreshResearchWorkerOutputV1:
        try:
            raw_result = await self._executor(work_item)
            if (
                isinstance(raw_result, dict)
                and "request_id" not in raw_result
                and "record_id" in raw_result
            ):
                result = research_run_guide_result_from_pipeline_output(
                    work_item,
                    raw_result,
                )
            else:
                result = ResearchRunGuideResultV1.model_validate(raw_result)
        except ResearchRunOfflineError as error:
            finished_at = _aware(self._now(), "guide refresh worker clock")
            receipt = _failure_receipt(
                work_item,
                status="offline",
                detail=f"research.run 网络不可用：{error}",
                finished_at=finished_at,
            )
            return GuideRefreshResearchWorkerOutputV1(
                request_id=work_item.request.id,
                receipt=receipt,
            )
        except ResearchRunSourceUnavailableError as error:
            finished_at = _aware(self._now(), "guide refresh worker clock")
            receipt = _failure_receipt(
                work_item,
                status="source_unavailable",
                detail=f"research.run 来源不可用：{error}",
                finished_at=finished_at,
            )
            return GuideRefreshResearchWorkerOutputV1(
                request_id=work_item.request.id,
                receipt=receipt,
            )
        except Exception as error:  # validation/runner failure is explicit, never a fake success
            finished_at = _aware(self._now(), "guide refresh worker clock")
            receipt = _failure_receipt(
                work_item,
                status="failed",
                detail=f"research.run 执行或结果校验失败：{type(error).__name__}: {error}",
                finished_at=finished_at,
            )
            return GuideRefreshResearchWorkerOutputV1(
                request_id=work_item.request.id,
                receipt=receipt,
            )

        if result.request_id != work_item.request.id:
            receipt = _failure_receipt(
                work_item,
                status="failed",
                detail="research.run 结果属于另一条攻略刷新请求。",
                finished_at=result.finished_at,
            )
            return GuideRefreshResearchWorkerOutputV1(
                request_id=work_item.request.id,
                receipt=receipt,
            )
        if result.status != "completed":
            receipt = _failure_receipt(
                work_item,
                status=result.status,
                detail=result.detail,
                finished_at=result.finished_at,
            )
            return GuideRefreshResearchWorkerOutputV1(
                request_id=work_item.request.id,
                receipt=receipt,
            )

        eligible: list[ResearchRunGuideSourceV1] = []
        incomplete: list[str] = []
        for index, source in enumerate(result.sources):
            missing = _missing_core_fields(source)
            if missing:
                incomplete.append(f"source[{index}]=" + ",".join(missing))
            else:
                eligible.append(source)
        if not eligible:
            receipt = _failure_receipt(
                work_item,
                status="source_unavailable",
                detail=(
                    "research.run 未产出可入库的来源原文与元数据；"
                    + "; ".join(incomplete)
                ),
                finished_at=result.finished_at,
            )
            return GuideRefreshResearchWorkerOutputV1(
                request_id=work_item.request.id,
                receipt=receipt,
            )

        try:
            bundle = _build_bundle(work_item, result, eligible)
        except Exception as error:
            receipt = _failure_receipt(
                work_item,
                status="failed",
                detail=f"research.run 严格来源无法组装攻略：{type(error).__name__}: {error}",
                finished_at=result.finished_at,
            )
            return GuideRefreshResearchWorkerOutputV1(
                request_id=work_item.request.id,
                receipt=receipt,
            )
        return GuideRefreshResearchWorkerOutputV1(
            request_id=work_item.request.id,
            bundle=bundle,
        )


async def consume_and_persist_guide_refresh(
    worker: GuideRefreshResearchWorker,
    work_item: GuideRefreshWorkItemV1,
    store: GuideRefreshResearchStore,
) -> GuideRefreshConsumeResultV1:
    """Commit one worker output through the queue's existing atomic store API."""

    output = await worker.consume(work_item)
    if output.bundle is not None:
        receipt, inserted = store.complete_guide_refresh_request(output.bundle)
        return GuideRefreshConsumeResultV1(receipt=receipt, inserted=inserted)
    assert output.receipt is not None
    receipt = store.append_guide_refresh_receipt(output.receipt)
    return GuideRefreshConsumeResultV1(receipt=receipt, inserted={})


def current_research_run_capability_gap() -> dict[str, Any]:
    """Machine-readable capability boundary for unattended guide research."""

    return {
        "schema": "game-observatory.ai-player.research-run-capability-gap.v1",
        "pipeline": "research.run",
        "strict_guide_bundle_supported": True,
        "online_validation": "pending",
        "current_source_fields": [
            "title",
            "url",
            "snippet",
            "platform",
            "publisher",
            "author",
            "author_or_publisher",
            "published_at",
            "updated_at",
            "retrieved_at",
            "game_version",
            "season",
            "server_stage",
            "citation_locator",
            "snapshot_text",
            "snapshot_sha256",
            "summary",
            "fresh_until",
        ],
        "pending_rule": (
            "Missing source applicability or freshness remains unverified and cannot "
            "drive an action; missing core provenance produces source_unavailable."
        ),
        "required_missing_fields": [],
        "former_gap_reason": (
            "当前 NativeResearch 固定输出 schema 只保留 title/url/snippet；"
            "LibraryWrite 没有把原文传给 snapshot_texts。"
        ),
        "reason": (
            "NativeResearch requests page-level provenance; the runner stamps retrieval "
            "time and SHA; LibraryWrite persists snapshots; the guide adapter consumes "
            "the exact run-local artifact. Real network output quality still needs a live run."
        ),
    }


__all__ = [
    "GuideRefreshConsumeResultV1",
    "GuideRefreshResearchWorker",
    "GuideRefreshResearchWorkerOutputV1",
    "ResearchRunGuideResultV1",
    "ResearchRunGuideSourceV1",
    "ResearchRunOfflineError",
    "ResearchRunSourceUnavailableError",
    "consume_and_persist_guide_refresh",
    "current_research_run_capability_gap",
    "research_run_guide_result_from_pipeline_output",
]
