"""Review capability catalog and on-demand submission guidance.

This module deliberately separates two axes:

* ``kind`` remains the Format-backed carrier (html/markdown/image/...).
* ``profile_id`` describes the review scenario (AIGC comparison, spreadsheet,
  workflow run, game content, ...).

The catalog is an implementation projection over registered carrier kinds.  It
does not register new Material kinds and therefore cannot bypass FormatRegistry.

Guidance is resolved only for an explicit event such as
``submission_preflight`` or ``embed_preflight``.  Nothing in this module is a
session-start or per-tool-call hook.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .material_types import (
    ATTACHMENT_ONLY_REVIEW_KINDS,
    default_review_format_registry,
    registered_review_kinds,
    registered_review_tiers,
)


CATALOG_VERSION = 3
REVIEW_MATERIAL_FORMAT_ID = "omni.review-material"
REVIEW_FACILITY_HANDBOOK = "docs/standards/review/审阅设施手册.md"
_EMBED_TEXT_CARRIERS = frozenset({
    "markdown",
    "plan",
    "agent-workflow-report",
    "html",
    "static-report",
    "demo",
    "custom_web_template",
})
_VISUAL_REFERENCE_RELATIONS = frozenset({
    "embedded_review",
    "comparison_member",
    "candidate",
})
_MARKDOWN_EMBED_RE = re.compile(
    r"(?<!\\)!\[\[\s*(omni://review/[^\]|\s]+)(?:\|[^\]]*)?\]\]",
    re.IGNORECASE,
)
_MARKDOWN_FENCE_RE = re.compile(
    r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$",
)
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
_MAX_EMBED_PREFLIGHT_BYTES = 16 * 1024 * 1024


class CapabilityStatus(str, Enum):
    available = "available"
    partial = "partial"
    planned = "planned"


class RoutingLevel(str, Enum):
    deterministic = "L1"
    suggestion = "L2"
    human_required = "L3"


class ReminderEvent(str, Enum):
    submission_preflight = "submission_preflight"
    embed_preflight = "embed_preflight"
    material_open = "material_open"


class ReminderSeverity(str, Enum):
    info = "info"
    warning = "warning"
    blocking = "blocking"


class ReferenceRelation(str, Enum):
    source = "source"
    evidence = "evidence"
    candidate = "candidate"
    comparison_member = "comparison_member"
    embedded_review = "embedded_review"
    external_surface = "external_surface"
    related = "related"


@dataclass(frozen=True)
class ReviewReference:
    """A typed association used by a review scenario.

    ``target`` should normally be a canonical ``omni://...`` reference.  HTTP
    URLs are accepted for external systems whose canonical Material identity
    has not been created yet.
    """

    target: str
    relation: ReferenceRelation | str = ReferenceRelation.related
    label: str = ""

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValueError("review reference target cannot be empty")
        relation = self.relation.value if isinstance(self.relation, ReferenceRelation) else str(self.relation)
        if relation not in {item.value for item in ReferenceRelation}:
            raise ValueError(f"unknown review reference relation: {relation!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.strip(),
            "relation": self.relation.value if isinstance(self.relation, ReferenceRelation) else str(self.relation),
            "label": self.label.strip(),
        }

    @classmethod
    def from_value(cls, value: ReviewReference | Mapping[str, Any] | str) -> ReviewReference:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(target=value)
        if not isinstance(value, Mapping):
            raise ValueError("review reference must be a string or object")
        return cls(
            target=str(value.get("target") or value.get("ref") or ""),
            relation=str(value.get("relation") or ReferenceRelation.related.value),
            label=str(value.get("label") or ""),
        )


@dataclass(frozen=True)
class ReviewReminder:
    code: str
    event: ReminderEvent | str
    severity: ReminderSeverity | str
    message: str
    field_path: str = ""
    suggested_profile_id: str = ""

    @property
    def blocking(self) -> bool:
        severity = self.severity.value if isinstance(self.severity, ReminderSeverity) else str(self.severity)
        return severity == ReminderSeverity.blocking.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "event": self.event.value if isinstance(self.event, ReminderEvent) else str(self.event),
            "severity": self.severity.value if isinstance(self.severity, ReminderSeverity) else str(self.severity),
            "message": self.message,
            "field_path": self.field_path,
            "suggested_profile_id": self.suggested_profile_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReviewReminder:
        return cls(
            code=str(value.get("code") or ""),
            event=str(value.get("event") or ReminderEvent.submission_preflight.value),
            severity=str(value.get("severity") or ReminderSeverity.info.value),
            message=str(value.get("message") or ""),
            field_path=str(value.get("field_path") or ""),
            suggested_profile_id=str(value.get("suggested_profile_id") or ""),
        )


@dataclass(frozen=True)
class ReviewResolutionTrace:
    selected_by: str
    routing_level: RoutingLevel | str
    confidence: float
    reason: str
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_by": self.selected_by,
            "routing_level": (
                self.routing_level.value
                if isinstance(self.routing_level, RoutingLevel)
                else str(self.routing_level)
            ),
            "confidence": round(max(0.0, min(float(self.confidence), 1.0)), 4),
            "reason": self.reason,
            "candidates": list(self.candidates),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> ReviewResolutionTrace:
        data = value or {}
        return cls(
            selected_by=str(data.get("selected_by") or "explicit"),
            routing_level=str(data.get("routing_level") or RoutingLevel.deterministic.value),
            confidence=float(data.get("confidence", 1.0)),
            reason=str(data.get("reason") or "explicit review context"),
            candidates=tuple(str(item) for item in (data.get("candidates") or []) if str(item)),
        )


@dataclass(frozen=True)
class ReviewContext:
    """First-class review metadata persisted on Material."""

    profile_id: str
    profile_version: int = 1
    schema_id: str = ""
    references: tuple[ReviewReference, ...] = ()
    resolution: ReviewResolutionTrace = field(
        default_factory=lambda: ReviewResolutionTrace(
            selected_by="explicit",
            routing_level=RoutingLevel.deterministic,
            confidence=1.0,
            reason="explicit review context",
        )
    )
    reminders: tuple[ReviewReminder, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("review_context.profile_id cannot be empty")
        if int(self.profile_version) <= 0:
            raise ValueError("review_context.profile_version must be positive")

    @property
    def blocked(self) -> bool:
        return any(item.blocking for item in self.reminders)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id.strip(),
            "profile_version": int(self.profile_version),
            "schema_id": self.schema_id.strip(),
            "references": [item.to_dict() for item in self.references],
            "resolution": self.resolution.to_dict(),
            "reminders": [item.to_dict() for item in self.reminders],
        }

    @classmethod
    def from_value(cls, value: ReviewContext | Mapping[str, Any]) -> ReviewContext:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("review_context must be an object")
        return cls(
            profile_id=str(value.get("profile_id") or ""),
            profile_version=int(value.get("profile_version") or 1),
            schema_id=str(value.get("schema_id") or ""),
            references=tuple(
                ReviewReference.from_value(item)
                for item in (value.get("references") or [])
            ),
            resolution=ReviewResolutionTrace.from_dict(
                value.get("resolution") if isinstance(value.get("resolution"), Mapping) else None
            ),
            reminders=tuple(
                ReviewReminder.from_dict(item)
                for item in (value.get("reminders") or [])
                if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True)
class ContextRequirement:
    code: str
    label: str
    any_of: tuple[str, ...]
    message: str
    severity: ReminderSeverity | str = ReminderSeverity.warning
    minimum_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "any_of": list(self.any_of),
            "message": self.message,
            "severity": self.severity.value if isinstance(self.severity, ReminderSeverity) else str(self.severity),
            "minimum_count": self.minimum_count,
        }


@dataclass(frozen=True)
class DetectorSpec:
    """A deterministic or advisory scenario detector.

    All declared carrier constraints must match.  At least one declared signal
    (schema id, structured extra key, suffix, or content marker) must match.
    """

    detector_id: str
    carrier_kinds: tuple[str, ...] = ()
    schema_ids: tuple[str, ...] = ()
    extra_any: tuple[str, ...] = ()
    file_suffixes: tuple[str, ...] = ()
    content_markers: tuple[str, ...] = ()
    routing_level: RoutingLevel | str = RoutingLevel.deterministic
    confidence: float = 1.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_id": self.detector_id,
            "carrier_kinds": list(self.carrier_kinds),
            "schema_ids": list(self.schema_ids),
            "extra_any": list(self.extra_any),
            "file_suffixes": list(self.file_suffixes),
            "content_markers": list(self.content_markers),
            "routing_level": (
                self.routing_level.value
                if isinstance(self.routing_level, RoutingLevel)
                else str(self.routing_level)
            ),
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReviewCapability:
    profile_id: str
    version: int
    title: str
    description: str
    status: CapabilityStatus | str
    accepted_carriers: tuple[str, ...]
    schema_ids: tuple[str, ...] = ()
    renderer_id: str = ""
    embed_renderer_id: str = ""
    fallback_profile_id: str = ""
    fallback_profiles: tuple[tuple[str, str], ...] = ()
    standard_paths: tuple[str, ...] = ()
    context_requirements: tuple[ContextRequirement, ...] = ()
    reference_relations: tuple[ReferenceRelation | str, ...] = ()
    comment_anchors: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    reminder_events: tuple[ReminderEvent | str, ...] = (
        ReminderEvent.submission_preflight,
    )
    detectors: tuple[DetectorSpec, ...] = ()
    material_format_id: str = REVIEW_MATERIAL_FORMAT_ID

    def fallback_for(self, carrier_kind: str) -> str:
        by_carrier = dict(self.fallback_profiles)
        return by_carrier.get(carrier_kind, self.fallback_profile_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, CapabilityStatus) else str(self.status),
            "material_format_id": self.material_format_id,
            "accepted_carriers": list(self.accepted_carriers),
            "schema_ids": list(self.schema_ids),
            "renderer_id": self.renderer_id,
            "embed_renderer_id": self.embed_renderer_id,
            "fallback_profile_id": self.fallback_profile_id,
            "fallback_by_carrier": dict(self.fallback_profiles),
            "standard_paths": list(self.standard_paths),
            "context_requirements": [item.to_dict() for item in self.context_requirements],
            "reference_relations": [
                item.value if isinstance(item, ReferenceRelation) else str(item)
                for item in self.reference_relations
            ],
            "comment_anchors": list(self.comment_anchors),
            "actions": list(self.actions),
            "reminder_events": [
                item.value if isinstance(item, ReminderEvent) else str(item)
                for item in self.reminder_events
            ],
            "detectors": [item.to_dict() for item in self.detectors],
        }


@dataclass(frozen=True)
class ReviewSubmissionIntent:
    kind: str
    tier: str = ""
    title: str = ""
    source_plan_id: str = ""
    source_subagent_id: str = ""
    file_path: str = ""
    inline_content: str = ""
    data_schema_id: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)
    project: str = ""
    track: str = ""
    version: int | None = None
    version_family: str = ""
    subject_id: str = ""
    subject_type: str = ""
    revision: int | None = None
    links: Mapping[str, Any] = field(default_factory=dict)
    review_context: ReviewContext | Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReviewSubmissionIntent:
        extra = value.get("extra")
        if not isinstance(extra, Mapping):
            extra = {}
        links = value.get("links")
        if not isinstance(links, Mapping):
            links = {}
        return cls(
            kind=str(value.get("kind") or ""),
            tier=str(value.get("tier") or ""),
            title=str(value.get("title") or ""),
            source_plan_id=str(value.get("source_plan_id") or ""),
            source_subagent_id=str(value.get("source_subagent_id") or ""),
            file_path=str(value.get("file_path") or value.get("file_relpath") or ""),
            inline_content=str(value.get("inline_content") or ""),
            data_schema_id=str(
                value.get("data_schema_id")
                or extra.get("data_schema_id")
                or ""
            ),
            extra=extra,
            project=str(value.get("project") or ""),
            track=str(value.get("track") or ""),
            version=value.get("version"),
            version_family=str(value.get("version_family") or ""),
            subject_id=str(value.get("subject_id") or ""),
            subject_type=str(value.get("subject_type") or ""),
            revision=value.get("revision"),
            links=links,
            review_context=value.get("review_context"),
        )


_AIGC_CANDIDATE_ORIGIN_KEYS = frozenset({
    "aigc_lab_card_id",
    "aigc_lab_candidate_id",
    "aigc_lab_url",
    "candidate_id",
    "generation_run_id",
})


def is_structured_aigc_candidate_registration(
    *,
    kind: str,
    extra: Mapping[str, Any] | None,
    review_context: ReviewContext | Mapping[str, Any] | None,
) -> bool:
    """Whether an internal AIGC candidate may be registered before its report.

    Ordinary images remain attachment-only.  A dedicated ``aigc-image`` node is
    different: a comparison producer must register candidate identities first
    so the report can reference canonical Material IDs.  The exception is
    deliberately narrow and still keeps the candidate out of the ordinary
    review queue.
    """

    if str(kind or "").strip() != "aigc-image":
        return False

    context: ReviewContext | None = None
    if review_context:
        try:
            context = ReviewContext.from_value(review_context)
        except (TypeError, ValueError):
            return False
        if context.profile_id != "aigc-candidate":
            return False

    extra_values = extra if isinstance(extra, Mapping) else {}
    has_declared_origin = any(
        str(extra_values.get(key) or "").strip()
        for key in _AIGC_CANDIDATE_ORIGIN_KEYS
    )
    has_candidate_reference = bool(
        context
        and any(
            (
                ref.relation.value
                if isinstance(ref.relation, ReferenceRelation)
                else str(ref.relation)
            )
            == ReferenceRelation.candidate.value
            and str(ref.target or "").strip()
            for ref in context.references
        )
    )
    return has_declared_origin or has_candidate_reference


@dataclass(frozen=True)
class ReviewSubmissionResolution:
    context: ReviewContext
    capability: ReviewCapability

    @property
    def blocked(self) -> bool:
        return self.context.blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_version": CATALOG_VERSION,
            "delivery": "on_demand",
            "blocked": self.blocked,
            "review_context": self.context.to_dict(),
            "capability": self.capability.to_dict(),
            "facility_discovery": _facility_discovery(
                self.context,
                self.capability,
            ),
        }


_REVIEW_STANDARD = "docs/standards/review/审阅与推送规范.md"
_TRIAGE_STANDARD = "docs/standards/review/发现分诊三级规范.md"
_MATERIAL_STANDARD = "docs/standards/concepts/material.md"


def _req(
    code: str,
    label: str,
    any_of: Sequence[str],
    message: str,
    severity: ReminderSeverity = ReminderSeverity.warning,
    minimum_count: int = 1,
) -> ContextRequirement:
    return ContextRequirement(
        code=code,
        label=label,
        any_of=tuple(any_of),
        message=message,
        severity=severity,
        minimum_count=minimum_count,
    )


DEFAULT_REVIEW_CAPABILITIES: tuple[ReviewCapability, ...] = (
    ReviewCapability(
        profile_id="generic-image",
        version=1,
        title="通用图片审阅",
        description="普通图片与截图的基础查看、区域标注和版本审阅。",
        status=CapabilityStatus.available,
        accepted_carriers=("image", "aigc-image"),
        renderer_id="carrier",
        embed_renderer_id="kind:image",
        standard_paths=(_REVIEW_STANDARD,),
        comment_anchors=("region",),
        actions=("comment", "verdict", "open_source"),
    ),
    ReviewCapability(
        profile_id="generic-document",
        version=1,
        title="通用文档审阅",
        description="Markdown、计划与 Agent 报告的阅读、文本选择和行级评论。",
        status=CapabilityStatus.available,
        accepted_carriers=("markdown", "plan", "agent-workflow-report"),
        renderer_id="carrier",
        embed_renderer_id="kind:markdown",
        standard_paths=(_REVIEW_STANDARD,),
        reference_relations=(
            ReferenceRelation.source,
            ReferenceRelation.evidence,
            ReferenceRelation.embedded_review,
        ),
        comment_anchors=("text_selection", "line_range"),
        actions=("comment", "verdict", "open_source"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.embed_preflight),
    ),
    ReviewCapability(
        profile_id="generic-web",
        version=1,
        title="通用网页审阅",
        description="HTML、静态报告、Demo 与自定义网页载体的安全展示和元素评论。",
        status=CapabilityStatus.available,
        accepted_carriers=("html", "static-report", "demo", "custom_web_template"),
        renderer_id="carrier",
        standard_paths=(_REVIEW_STANDARD,),
        reference_relations=(
            ReferenceRelation.source,
            ReferenceRelation.evidence,
            ReferenceRelation.embedded_review,
        ),
        comment_anchors=("element", "text_selection", "viewport_region"),
        actions=("comment", "verdict", "open_source", "open_live_surface"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.embed_preflight),
    ),
    ReviewCapability(
        profile_id="generic-structured",
        version=1,
        title="通用结构化审阅",
        description="问题、规格和决策候选等结构化材料的安全 fallback。",
        status=CapabilityStatus.available,
        accepted_carriers=("key_question", "webgame-spec", "decision-candidate"),
        renderer_id="carrier",
        embed_renderer_id="kind:key_question",
        standard_paths=(_REVIEW_STANDARD,),
        comment_anchors=("field", "item"),
        actions=("comment", "verdict", "open_source"),
    ),
    ReviewCapability(
        profile_id="generic-video",
        version=1,
        title="通用视频审阅",
        description="视频播放、时间点/帧评论、裁决和来源跳转。",
        status=CapabilityStatus.available,
        accepted_carriers=("video",),
        renderer_id="carrier",
        embed_renderer_id="kind:video",
        standard_paths=(_REVIEW_STANDARD,),
        comment_anchors=("timestamp", "frame_region"),
        actions=("comment", "verdict", "open_source"),
    ),
    ReviewCapability(
        profile_id="aigc-candidate",
        version=1,
        title="AIGC 单候选审阅",
        description="审阅单个生成候选及其生成 run、prompt、参考资产和候选身份。",
        status=CapabilityStatus.available,
        accepted_carriers=("aigc-image", "image"),
        renderer_id="kind:aigc-image",
        embed_renderer_id="profile:aigc-candidate",
        fallback_profile_id="generic-image",
        standard_paths=(_REVIEW_STANDARD, _TRIAGE_STANDARD),
        context_requirements=(
            _req(
                "aigc_origin_missing",
                "AIGC 生成来源",
                (
                    "extra.aigc_lab_card_id",
                    "extra.aigc_lab_url",
                    "extra.candidate_id",
                    "extra.generation_run_id",
                    "reference:candidate",
                ),
                "AIGC 候选缺少 card/candidate/run 或候选材料引用；可以查看图片，但无法还原生成背景。",
            ),
        ),
        reference_relations=(
            ReferenceRelation.source,
            ReferenceRelation.candidate,
            ReferenceRelation.evidence,
        ),
        comment_anchors=("region", "candidate"),
        actions=("comment", "verdict", "keep", "reject_candidate", "continue_generation"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.material_open),
        detectors=(
            DetectorSpec(
                detector_id="aigc-kind",
                carrier_kinds=("aigc-image",),
                content_markers=("*",),
                confidence=1.0,
                routing_level=RoutingLevel.deterministic,
                reason="carrier kind is aigc-image",
            ),
            DetectorSpec(
                detector_id="aigc-candidate-identity",
                carrier_kinds=("image", "aigc-image"),
                extra_any=(
                    "aigc_lab_card_id",
                    "aigc_lab_url",
                    "candidate_id",
                    "generation_run_id",
                ),
                confidence=0.98,
                routing_level=RoutingLevel.deterministic,
                reason="structured AIGC candidate identity is present",
            ),
        ),
    ),
    ReviewCapability(
        profile_id="aigc-comparison",
        version=1,
        title="AIGC 候选比较审阅",
        description="HTML/Markdown 比较报告保持原载体，同时装配候选、生成背景和选择动作。",
        status=CapabilityStatus.partial,
        accepted_carriers=("html", "static-report", "markdown"),
        renderer_id="",
        embed_renderer_id="profile:aigc-comparison",
        fallback_profiles=(
            ("html", "generic-web"),
            ("static-report", "generic-web"),
            ("markdown", "generic-document"),
        ),
        standard_paths=(_REVIEW_STANDARD, _TRIAGE_STANDARD),
        context_requirements=(
            _req(
                "aigc_comparison_members_missing",
                "比较候选引用",
                (
                    "extra.aigc_candidate_refs",
                    "reference:comparison_member",
                    "reference:candidate",
                ),
                "AIGC 比较报告需要至少两个结构化候选引用；不要只在 HTML 中留下裸图片或 AIGC Lab 链接。",
                minimum_count=2,
            ),
        ),
        reference_relations=(
            ReferenceRelation.comparison_member,
            ReferenceRelation.candidate,
            ReferenceRelation.source,
        ),
        comment_anchors=("candidate", "element", "text_selection"),
        actions=("comment", "verdict", "keep", "reject_candidate", "continue_generation"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.embed_preflight),
        detectors=(
            DetectorSpec(
                detector_id="aigc-comparison-refs",
                carrier_kinds=("html", "static-report", "markdown"),
                extra_any=("aigc_candidate_refs", "candidate_group_id"),
                confidence=0.98,
                routing_level=RoutingLevel.deterministic,
                reason="structured candidate comparison references are present",
            ),
            DetectorSpec(
                detector_id="aigc-comparison-content",
                carrier_kinds=("html", "static-report", "markdown"),
                content_markers=("aigc-lab", "/api/cards", "candidate_id", "candidate-id"),
                confidence=0.72,
                routing_level=RoutingLevel.suggestion,
                reason="content looks like an AIGC comparison but has no structured declaration",
            ),
        ),
    ),
    ReviewCapability(
        profile_id="spreadsheet-review",
        version=1,
        title="表格与工作簿审阅",
        description="审阅 workbook/sheet/range、公式和值 diff，并把评论锚定到单元格或区域。",
        status=CapabilityStatus.partial,
        accepted_carriers=("html", "static-report", "custom_web_template"),
        schema_ids=("spreadsheet_review_v1", "table_diff_v1"),
        renderer_id="",
        fallback_profile_id="generic-web",
        standard_paths=(_REVIEW_STANDARD, _MATERIAL_STANDARD),
        context_requirements=(
            _req(
                "spreadsheet_source_missing",
                "工作簿真源",
                ("extra.workbook_ref", "extra.source_workbook", "reference:source"),
                "表格审阅缺少工作簿真源引用；Web 视图不能成为第二套表格真源。",
            ),
        ),
        reference_relations=(ReferenceRelation.source, ReferenceRelation.evidence),
        comment_anchors=("cell", "range", "row", "column"),
        actions=("comment", "verdict", "filter", "compare_revision", "request_edit"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.embed_preflight),
        detectors=(
            DetectorSpec(
                detector_id="spreadsheet-schema",
                carrier_kinds=("html", "static-report", "custom_web_template"),
                schema_ids=("spreadsheet_review_v1", "table_diff_v1"),
                confidence=1.0,
                routing_level=RoutingLevel.deterministic,
                reason="registered spreadsheet review schema is present",
            ),
            DetectorSpec(
                detector_id="spreadsheet-context",
                carrier_kinds=("html", "static-report", "custom_web_template"),
                extra_any=("workbook_ref", "source_workbook", "sheet_name", "cell_range"),
                confidence=0.95,
                routing_level=RoutingLevel.deterministic,
                reason="structured workbook/sheet context is present",
            ),
            DetectorSpec(
                detector_id="spreadsheet-source-file",
                file_suffixes=(".xls", ".xlsx", ".xlsm", ".csv"),
                confidence=0.7,
                routing_level=RoutingLevel.suggestion,
                reason="source path is a spreadsheet; submit a Web review representation plus source reference",
            ),
        ),
    ),
    ReviewCapability(
        profile_id="workflow-review",
        version=1,
        title="工作流运行审阅",
        description="审阅 DAG、节点状态、输入输出、失败证据和重跑动作。",
        status=CapabilityStatus.partial,
        accepted_carriers=("html", "static-report", "custom_web_template", "agent-workflow-report"),
        schema_ids=("workflow_run_v1", "workflow_diff_v1"),
        renderer_id="",
        fallback_profiles=(
            ("html", "generic-web"),
            ("static-report", "generic-web"),
            ("custom_web_template", "generic-web"),
            ("agent-workflow-report", "generic-document"),
        ),
        standard_paths=(_REVIEW_STANDARD, _MATERIAL_STANDARD),
        context_requirements=(
            _req(
                "workflow_identity_missing",
                "工作流运行身份",
                ("extra.workflow_run_id", "extra.workflow_id", "reference:source"),
                "工作流审阅缺少 workflow/run 身份，后续无法重跑、比较或追踪节点产物。",
            ),
        ),
        reference_relations=(ReferenceRelation.source, ReferenceRelation.evidence),
        comment_anchors=("node", "edge", "run_step", "log_range"),
        actions=("comment", "verdict", "inspect_node", "rerun_failed", "compare_run"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.embed_preflight),
        detectors=(
            DetectorSpec(
                detector_id="workflow-schema",
                schema_ids=("workflow_run_v1", "workflow_diff_v1"),
                confidence=1.0,
                routing_level=RoutingLevel.deterministic,
                reason="registered workflow review schema is present",
            ),
            DetectorSpec(
                detector_id="workflow-context",
                extra_any=("workflow_run_id", "workflow_id", "workflow_nodes"),
                confidence=0.96,
                routing_level=RoutingLevel.deterministic,
                reason="structured workflow identity is present",
            ),
        ),
    ),
    ReviewCapability(
        profile_id="game-content-review",
        version=1,
        title="游戏内容 / MC / Prefab 审阅",
        description="承载场景、实体、资产、状态时间线、截图/视频和结构化 diff；MC 的正式边界仍需真实案例确认。",
        status=CapabilityStatus.planned,
        accepted_carriers=("html", "static-report", "demo", "image", "video", "custom_web_template"),
        schema_ids=("game_content_review_v1", "prefab_review_v1"),
        renderer_id="",
        fallback_profiles=(
            ("html", "generic-web"),
            ("static-report", "generic-web"),
            ("demo", "generic-web"),
            ("image", "generic-image"),
            ("video", "generic-video"),
            ("custom_web_template", "generic-web"),
        ),
        standard_paths=(_REVIEW_STANDARD, _MATERIAL_STANDARD),
        context_requirements=(
            _req(
                "game_subject_missing",
                "游戏内容主体",
                (
                    "subject_id",
                    "extra.prefab_ref",
                    "extra.scene_ref",
                    "extra.entity_ref",
                    "reference:source",
                ),
                "游戏内容审阅缺少场景、Prefab、实体或正式 subject 引用。",
            ),
        ),
        reference_relations=(ReferenceRelation.source, ReferenceRelation.evidence),
        comment_anchors=("entity", "asset", "state", "timestamp", "region"),
        actions=("comment", "verdict", "open_runtime", "request_change", "compare_revision"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.embed_preflight),
        detectors=(
            DetectorSpec(
                detector_id="game-content-schema",
                schema_ids=("game_content_review_v1", "prefab_review_v1"),
                confidence=1.0,
                routing_level=RoutingLevel.deterministic,
                reason="registered game content review schema is present",
            ),
            DetectorSpec(
                detector_id="game-content-context",
                extra_any=("prefab_ref", "scene_ref", "entity_ref", "runtime_capture_id"),
                confidence=0.96,
                routing_level=RoutingLevel.deterministic,
                reason="structured game content identity is present",
            ),
        ),
    ),
    ReviewCapability(
        profile_id="feishu-authoring-review",
        version=1,
        title="collab platform编写与回写审阅",
        description="审阅collab platform文档 block、表格 range、Wiki node 或消息卡片，并区分草稿、写入和发布。",
        status=CapabilityStatus.planned,
        accepted_carriers=("markdown", "html", "static-report", "custom_web_template"),
        schema_ids=("feishu_authoring_review_v1",),
        renderer_id="",
        fallback_profiles=(
            ("markdown", "generic-document"),
            ("html", "generic-web"),
            ("static-report", "generic-web"),
            ("custom_web_template", "generic-web"),
        ),
        standard_paths=(_REVIEW_STANDARD, _MATERIAL_STANDARD),
        context_requirements=(
            _req(
                "feishu_target_missing",
                "collab platform远端对象",
                (
                    "extra.feishu_doc_token",
                    "extra.feishu_sheet_token",
                    "extra.feishu_wiki_token",
                    "reference:external_surface",
                ),
                "collab platform编写审阅缺少远端对象引用；无法判断是草稿、已写入还是已发布版本。",
            ),
        ),
        reference_relations=(
            ReferenceRelation.source,
            ReferenceRelation.external_surface,
        ),
        comment_anchors=("block", "cell", "range", "wiki_node", "card_element"),
        actions=("comment", "verdict", "write_draft", "request_publish"),
        reminder_events=(ReminderEvent.submission_preflight, ReminderEvent.embed_preflight),
        detectors=(
            DetectorSpec(
                detector_id="feishu-schema",
                schema_ids=("feishu_authoring_review_v1",),
                confidence=1.0,
                routing_level=RoutingLevel.deterministic,
                reason="registered Feishu authoring schema is present",
            ),
            DetectorSpec(
                detector_id="feishu-context",
                extra_any=(
                    "feishu_doc_token",
                    "feishu_sheet_token",
                    "feishu_wiki_token",
                    "feishu_message_id",
                ),
                confidence=0.96,
                routing_level=RoutingLevel.deterministic,
                reason="structured Feishu target identity is present",
            ),
        ),
    ),
)


_FALLBACK_BY_KIND: dict[str, str] = {
    "image": "generic-image",
    "aigc-image": "generic-image",
    "markdown": "generic-document",
    "plan": "generic-document",
    "agent-workflow-report": "generic-document",
    "html": "generic-web",
    "static-report": "generic-web",
    "demo": "generic-web",
    "custom_web_template": "generic-web",
    "key_question": "generic-structured",
    "webgame-spec": "generic-structured",
    "decision-candidate": "generic-structured",
    "video": "generic-video",
}


def review_capabilities() -> tuple[ReviewCapability, ...]:
    return DEFAULT_REVIEW_CAPABILITIES


def review_capability_map() -> dict[str, ReviewCapability]:
    return {item.profile_id: item for item in review_capabilities()}


def validate_review_capabilities(registry=None) -> list[str]:
    """Return deterministic catalog drift errors."""

    if registry is None:
        registry = default_review_format_registry()
    errors: list[str] = []
    profiles = review_capability_map()
    if len(profiles) != len(review_capabilities()):
        errors.append("duplicate review capability profile_id")
    registered_kinds = registered_review_kinds(registry)
    for capability in review_capabilities():
        if registry is not None and hasattr(registry, "is_registered"):
            if not registry.is_registered(capability.material_format_id):
                errors.append(
                    f"{capability.profile_id}: material format {capability.material_format_id!r} is not registered"
                )
        unknown_carriers = sorted(set(capability.accepted_carriers) - registered_kinds)
        if unknown_carriers:
            errors.append(
                f"{capability.profile_id}: unregistered carriers {', '.join(unknown_carriers)}"
            )
        if capability.fallback_profile_id and capability.fallback_profile_id not in profiles:
            errors.append(
                f"{capability.profile_id}: unknown fallback {capability.fallback_profile_id!r}"
            )
        fallback_by_carrier = dict(capability.fallback_profiles)
        if len(fallback_by_carrier) != len(capability.fallback_profiles):
            errors.append(f"{capability.profile_id}: duplicate carrier in fallback_by_carrier")
        unknown_fallback_carriers = sorted(
            set(fallback_by_carrier) - set(capability.accepted_carriers)
        )
        if unknown_fallback_carriers:
            errors.append(
                f"{capability.profile_id}: fallback declared for unsupported carriers "
                f"{', '.join(unknown_fallback_carriers)}"
            )
        for carrier, fallback_id in fallback_by_carrier.items():
            fallback = profiles.get(fallback_id)
            if fallback is None:
                errors.append(
                    f"{capability.profile_id}: unknown fallback {fallback_id!r} for {carrier}"
                )
            elif carrier not in fallback.accepted_carriers:
                errors.append(
                    f"{capability.profile_id}: fallback {fallback_id!r} does not accept {carrier!r}"
                )
    uncovered = sorted(registered_kinds - set(_FALLBACK_BY_KIND))
    if uncovered:
        errors.append(f"registered review kinds without generic fallback: {', '.join(uncovered)}")
    return errors


def capability_catalog(registry=None) -> dict[str, Any]:
    errors = validate_review_capabilities(registry)
    return {
        "catalog_version": CATALOG_VERSION,
        "authority": {
            "material_semantics": "FormatRegistry",
            "review_instances": "MaterialStore",
            "catalog_role": "validated implementation projection",
        },
        "delivery": "on_demand",
        "facility_discovery": {
            "policy": "automatic_first",
            "manual_path": REVIEW_FACILITY_HANDBOOK,
            "load_policy": "only_when_resolution_requests_it_or_facility_authoring",
            "facility_shapes": [
                "compact_preview",
                "full_renderer",
                "dedicated_page",
                "generic_fallback",
            ],
        },
        "profiles": [item.to_dict() for item in review_capabilities()],
        "validation": {"ok": not errors, "errors": errors},
    }


def _facility_discovery(
    context: ReviewContext,
    capability: ReviewCapability,
) -> dict[str, Any]:
    """Tell an event-scoped caller whether automatic facility selection is enough.

    The handbook path is intentionally not injected into normal prompts.  It is
    returned by a resolution only when structured routing is advisory/ambiguous,
    an explicit declaration is invalid, or a declared schema fell through to a
    carrier fallback that the backend cannot verify against the frontend registry.
    """

    trace = context.resolution
    routing_level = (
        trace.routing_level.value
        if isinstance(trace.routing_level, RoutingLevel)
        else str(trace.routing_level)
    )
    manual_codes = {
        "carrier_kind_missing",
        "carrier_kind_unregistered",
        "review_context_invalid",
        "review_profile_unknown",
        "review_profile_carrier_mismatch",
        "review_profile_ambiguous",
    }
    unresolved_declaration = any(
        reminder.code in manual_codes
        for reminder in context.reminders
    )
    unverified_schema_fallback = bool(
        context.schema_id
        and trace.selected_by == "carrier_fallback"
    )
    consult_manual = (
        routing_level != RoutingLevel.deterministic.value
        or unresolved_declaration
        or unverified_schema_fallback
    )

    if unresolved_declaration:
        reason = "review declaration is missing, invalid, or conflicting"
    elif unverified_schema_fallback:
        reason = "declared schema is not verified by the backend capability projection"
    elif routing_level == RoutingLevel.suggestion.value:
        reason = "only advisory review signals matched"
    elif routing_level == RoutingLevel.human_required.value:
        reason = "multiple review facilities require an explicit semantic choice"
    else:
        reason = "structured signals selected the review facility automatically"

    search_terms: list[str] = []
    if consult_manual:
        for term in (
            context.schema_id,
            capability.profile_id,
            *trace.candidates,
        ):
            value = str(term or "").strip()
            if value and value not in search_terms:
                search_terms.append(value)

    return {
        "policy": "automatic_first",
        "selection": "manual_required" if consult_manual else "automatic",
        "consult_manual": consult_manual,
        "manual_path": REVIEW_FACILITY_HANDBOOK if consult_manual else "",
        "search_terms": search_terms,
        "reason": reason,
    }


def _field_present(
    intent: ReviewSubmissionIntent,
    field_path: str,
    references: Sequence[ReviewReference],
    minimum_count: int = 1,
) -> bool:
    if field_path.startswith("extra."):
        key = field_path.split(".", 1)[1]
        value = intent.extra.get(key)
        if value in (None, "", [], {}):
            return False
        if minimum_count > 1:
            return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= minimum_count
        return True
    if field_path.startswith("reference:"):
        relation = field_path.split(":", 1)[1]
        count = sum(
            (item.relation.value if isinstance(item.relation, ReferenceRelation) else str(item.relation))
            == relation
            for item in references
        )
        return count >= minimum_count
    value = getattr(intent, field_path, None)
    if value in (None, "", [], {}):
        return False
    if minimum_count > 1:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= minimum_count
    return True


def _detector_matches(detector: DetectorSpec, intent: ReviewSubmissionIntent) -> bool:
    if detector.carrier_kinds and intent.kind not in detector.carrier_kinds:
        return False
    signals_declared = bool(
        detector.schema_ids
        or detector.extra_any
        or detector.file_suffixes
        or detector.content_markers
    )
    if not signals_declared:
        return False
    if detector.schema_ids and intent.data_schema_id in detector.schema_ids:
        return True
    if detector.extra_any and any(
        intent.extra.get(key) not in (None, "", [], {})
        for key in detector.extra_any
    ):
        return True
    if detector.file_suffixes and intent.file_path:
        if Path(intent.file_path).suffix.lower() in detector.file_suffixes:
            return True
    if detector.content_markers:
        if detector.content_markers == ("*",):
            return True
        lower = intent.inline_content.lower()
        if any(marker.lower() in lower for marker in detector.content_markers):
            return True
    return False


def _review_material_id(raw: str) -> str:
    """Return a decoded Material id only for the canonical omni://review form."""

    try:
        parsed = urlsplit(str(raw or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() != "omni" or parsed.netloc.lower() != "review":
        return ""
    material_id = unquote(parsed.path.lstrip("/")).strip()
    return material_id if material_id else ""


def _canonical_review_ref(material_id: str) -> str:
    return f"omni://review/{quote(material_id, safe='')}"


class _EmbeddedReviewHTMLParser(HTMLParser):
    """Extract only declarative embeds that the Web renderer can actually mount."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.material_ids: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {str(key).lower(): value or "" for key, value in attrs}
        declared = _review_material_id(values.get("data-review-material-embed", ""))
        if declared:
            self.material_ids.add(declared)

        if tag.lower() != "iframe":
            return
        src = values.get("src", "").strip()
        if not src:
            return
        try:
            query = parse_qs(urlsplit(src).query)
        except ValueError:
            return
        if (query.get("surface") or [""])[0] != "material-embed":
            return
        material_id = str((query.get("id") or [""])[0]).strip()
        if material_id:
            self.material_ids.add(material_id)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)


def _intent_review_text(intent: ReviewSubmissionIntent) -> str:
    if intent.kind not in _EMBED_TEXT_CARRIERS:
        return ""
    if intent.inline_content:
        return intent.inline_content
    if not intent.file_path:
        return ""
    path = Path(intent.file_path)
    try:
        if not path.is_file() or path.stat().st_size > _MAX_EMBED_PREFLIGHT_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _embedded_review_placeholders(
    intent: ReviewSubmissionIntent,
) -> set[str]:
    content = _intent_review_text(intent)
    if not content:
        return set()

    inspectable = content
    if intent.kind in {"markdown", "plan", "agent-workflow-report"}:
        # Syntax examples inside fenced/inline code do not render as MaterialEmbed.
        inspectable = _MARKDOWN_FENCE_RE.sub("", inspectable)
        inspectable = _MARKDOWN_INLINE_CODE_RE.sub("", inspectable)

    material_ids = {
        material_id
        for raw in _MARKDOWN_EMBED_RE.findall(inspectable)
        if (material_id := _review_material_id(raw))
    }
    parser = _EmbeddedReviewHTMLParser()
    try:
        parser.feed(inspectable)
        parser.close()
    except Exception:
        # HTMLParser is best-effort for author input. Markdown extraction and
        # typed-reference validation still run; malformed HTML cannot crash submit.
        pass
    material_ids.update(parser.material_ids)
    return material_ids


def _embedded_review_consistency_reminders(
    intent: ReviewSubmissionIntent,
    event: ReminderEvent,
    references: Sequence[ReviewReference],
) -> list[ReviewReminder]:
    if event not in {
        ReminderEvent.submission_preflight,
        ReminderEvent.embed_preflight,
    }:
        return []

    placeholders = _embedded_review_placeholders(intent)
    typed_by_relation: dict[str, set[str]] = {}
    for reference in references:
        material_id = _review_material_id(reference.target)
        if not material_id:
            continue
        relation = (
            reference.relation.value
            if isinstance(reference.relation, ReferenceRelation)
            else str(reference.relation)
        )
        typed_by_relation.setdefault(relation, set()).add(material_id)

    visual_targets = set().union(*(
        typed_by_relation.get(relation, set())
        for relation in _VISUAL_REFERENCE_RELATIONS
    ))
    embedded_targets = typed_by_relation.get(
        ReferenceRelation.embedded_review.value,
        set(),
    )
    missing_typed_relation = sorted(placeholders - visual_targets)
    missing_body_placeholder = sorted(embedded_targets - placeholders)
    reminders: list[ReviewReminder] = []

    if missing_typed_relation:
        refs = ", ".join(_canonical_review_ref(item) for item in missing_typed_relation)
        reminders.append(
            ReviewReminder(
                code="embedded_review_relation_missing",
                event=event,
                severity=ReminderSeverity.blocking,
                message=(
                    "正文包含 MaterialEmbed 占位，但缺少对应 typed reference："
                    f"{refs}。通用文档/网页请补 relation=embedded_review；"
                    "候选比较可使用 profile 允许的 comparison_member/candidate。"
                ),
                field_path="review_context.references",
            )
        )
    if missing_body_placeholder:
        refs = ", ".join(_canonical_review_ref(item) for item in missing_body_placeholder)
        reminders.append(
            ReviewReminder(
                code="embedded_review_placeholder_missing",
                event=event,
                severity=ReminderSeverity.blocking,
                message=(
                    "review_context 声明了 embedded_review，但正文没有对应 canonical embed 占位："
                    f"{refs}。请补 Markdown wikilink 或 HTML declarative embed，"
                    "不要留下不可见的嵌入关系。"
                ),
                field_path="inline_content | file_path",
            )
        )
    return reminders


def _base_submission_reminders(
    intent: ReviewSubmissionIntent,
    event: ReminderEvent,
    registered_kinds: set[str],
    registered_tiers: set[str],
) -> list[ReviewReminder]:
    if event != ReminderEvent.submission_preflight:
        return []
    reminders: list[ReviewReminder] = []

    def add(code: str, severity: ReminderSeverity, message: str, field_path: str) -> None:
        reminders.append(
            ReviewReminder(
                code=code,
                event=event,
                severity=severity,
                message=message,
                field_path=field_path,
            )
        )

    if not intent.kind:
        add("carrier_kind_missing", ReminderSeverity.blocking, "提交前必须给出材料载体 kind。", "kind")
    elif intent.kind not in registered_kinds:
        add(
            "carrier_kind_unregistered",
            ReminderSeverity.blocking,
            f"kind {intent.kind!r} 未由 FormatRegistry 注册。",
            "kind",
        )
    if not intent.tier:
        add(
            "material_tier_missing",
            ReminderSeverity.blocking,
            "提交前必须给出材料审阅 tier。",
            "tier",
        )
    elif intent.tier not in registered_tiers:
        add(
            "material_tier_unregistered",
            ReminderSeverity.blocking,
            f"tier {intent.tier!r} 未由 FormatRegistry 注册。",
            "tier",
        )
    if not intent.project:
        add("project_missing", ReminderSeverity.blocking, "提交前必须明确材料所属 project。", "project")
    if not intent.track:
        add("track_missing", ReminderSeverity.blocking, "提交前必须明确材料所在 track/stage。", "track")
    if intent.version is None:
        add("version_missing", ReminderSeverity.blocking, "提交前必须给出材料 version。", "version")
    if not intent.file_path and not intent.inline_content:
        add(
            "review_content_missing",
            ReminderSeverity.blocking,
            "提交前必须提供 file_path/file_relpath 或 inline_content。",
            "file_path",
        )
    if (
        intent.kind in ATTACHMENT_ONLY_REVIEW_KINDS
        and not str(intent.links.get("parent") or "").strip()
        and not is_structured_aigc_candidate_registration(
            kind=intent.kind,
            extra=intent.extra,
            review_context=intent.review_context,
        )
    ):
        add(
            "image_parent_report_missing",
            ReminderSeverity.blocking,
            "图片不能作为顶层审阅材料；请先提交带说明和对照关系的报告，再通过 links.parent 挂载。",
            "links.parent",
        )
    if not intent.source_plan_id:
        add(
            "source_plan_missing",
            ReminderSeverity.warning,
            "材料缺少 source_plan_id；可以继续预检，但会失去计划关联与会话回读能力。",
            "source_plan_id",
        )
    return reminders


def _capability_event_enabled(capability: ReviewCapability, event: ReminderEvent) -> bool:
    return event.value in {
        item.value if isinstance(item, ReminderEvent) else str(item)
        for item in capability.reminder_events
    }


def resolve_review_submission(
    intent: ReviewSubmissionIntent | Mapping[str, Any],
    *,
    event: ReminderEvent | str = ReminderEvent.submission_preflight,
    registry=None,
) -> ReviewSubmissionResolution:
    """Resolve one review scenario without writing or installing a hook."""

    if not isinstance(intent, ReviewSubmissionIntent):
        intent = ReviewSubmissionIntent.from_mapping(intent)
    event_value = event if isinstance(event, ReminderEvent) else ReminderEvent(str(event))
    if registry is None:
        registry = default_review_format_registry()
    capabilities = review_capability_map()
    registered_kinds = registered_review_kinds(registry)
    registered_tiers = registered_review_tiers(registry)
    reminders = _base_submission_reminders(
        intent,
        event_value,
        registered_kinds,
        registered_tiers,
    )

    explicit: ReviewContext | None = None
    if intent.review_context:
        try:
            explicit = ReviewContext.from_value(intent.review_context)
        except (TypeError, ValueError) as exc:
            fallback_id = _FALLBACK_BY_KIND.get(intent.kind, "generic-structured")
            fallback = capabilities[fallback_id]
            reminders.append(
                ReviewReminder(
                    code="review_context_invalid",
                    event=event_value,
                    severity=ReminderSeverity.blocking,
                    message=f"review_context 结构无效：{exc}",
                    field_path="review_context",
                )
            )
            context = ReviewContext(
                profile_id=fallback.profile_id,
                profile_version=fallback.version,
                schema_id=intent.data_schema_id,
                resolution=ReviewResolutionTrace(
                    selected_by="carrier_fallback",
                    routing_level=RoutingLevel.human_required,
                    confidence=0.0,
                    reason="invalid explicit review context",
                ),
                reminders=tuple(reminders),
            )
            return ReviewSubmissionResolution(context=context, capability=fallback)

    selected: ReviewCapability | None = None
    selected_by = ""
    selected_level: RoutingLevel | str = RoutingLevel.deterministic
    selected_confidence = 0.0
    selected_reason = ""
    candidates: list[str] = []
    references = tuple(explicit.references) if explicit else ()

    if explicit:
        selected = capabilities.get(explicit.profile_id)
        if selected is None:
            fallback_id = _FALLBACK_BY_KIND.get(intent.kind, "generic-structured")
            selected = capabilities[fallback_id]
            reminders.append(
                ReviewReminder(
                    code="review_profile_unknown",
                    event=event_value,
                    severity=ReminderSeverity.blocking,
                    message=f"review profile {explicit.profile_id!r} 未注册。",
                    field_path="review_context.profile_id",
                )
            )
            selected_by = "explicit_invalid"
            selected_level = RoutingLevel.human_required
            selected_confidence = 0.0
            selected_reason = "explicit profile is unknown"
        elif intent.kind and intent.kind not in selected.accepted_carriers:
            reminders.append(
                ReviewReminder(
                    code="review_profile_carrier_mismatch",
                    event=event_value,
                    severity=ReminderSeverity.blocking,
                    message=(
                        f"profile {selected.profile_id!r} 不接受 carrier {intent.kind!r}；"
                        f"允许值：{', '.join(selected.accepted_carriers)}。"
                    ),
                    field_path="review_context.profile_id",
                )
            )
            selected_by = "explicit_invalid"
            selected_level = RoutingLevel.human_required
            selected_confidence = 0.0
            selected_reason = "explicit profile is incompatible with carrier"
        else:
            selected_by = "explicit"
            selected_level = RoutingLevel.deterministic
            selected_confidence = 1.0
            selected_reason = "producer explicitly declared review profile"

    if selected is None:
        deterministic_matches: list[tuple[float, ReviewCapability, DetectorSpec]] = []
        advisory_matches: list[tuple[float, ReviewCapability, DetectorSpec]] = []
        for capability in review_capabilities():
            if intent.kind and intent.kind not in capability.accepted_carriers:
                continue
            for detector in capability.detectors:
                if not _detector_matches(detector, intent):
                    continue
                level = (
                    detector.routing_level.value
                    if isinstance(detector.routing_level, RoutingLevel)
                    else str(detector.routing_level)
                )
                item = (detector.confidence, capability, detector)
                if level == RoutingLevel.deterministic.value:
                    deterministic_matches.append(item)
                else:
                    advisory_matches.append(item)

        deterministic_matches.sort(key=lambda item: (-item[0], item[1].profile_id))
        advisory_matches.sort(key=lambda item: (-item[0], item[1].profile_id))
        if deterministic_matches:
            distinct_matches: list[tuple[float, ReviewCapability, DetectorSpec]] = []
            seen_profiles: set[str] = set()
            for item in deterministic_matches:
                if item[1].profile_id in seen_profiles:
                    continue
                seen_profiles.add(item[1].profile_id)
                distinct_matches.append(item)
            best_score, best_capability, best_detector = distinct_matches[0]
            conflicts = [
                item
                for item in distinct_matches[1:]
                if item[0] >= best_score - 0.05
            ]
            if conflicts:
                fallback_id = _FALLBACK_BY_KIND.get(intent.kind, "generic-structured")
                selected = capabilities[fallback_id]
                candidates = [
                    best_capability.profile_id,
                    *(item[1].profile_id for item in conflicts),
                ]
                selected_by = "detector_conflict"
                selected_level = RoutingLevel.human_required
                selected_confidence = 0.0
                selected_reason = "multiple structured review profiles conflict"
                reminders.append(
                    ReviewReminder(
                        code="review_profile_ambiguous",
                        event=event_value,
                        severity=ReminderSeverity.blocking,
                        message=(
                            "材料同时命中多个强场景信号："
                            f"{', '.join(candidates)}。请显式填写 review_context.profile_id，"
                            "不要让自动分流替人做语义选择。"
                        ),
                        field_path="review_context.profile_id",
                    )
                )
            else:
                selected = best_capability
                selected_by = f"detector:{best_detector.detector_id}"
                selected_level = RoutingLevel.deterministic
                selected_confidence = best_score
                selected_reason = best_detector.reason
                candidates = [
                    capability.profile_id
                    for _, capability, _ in distinct_matches[1:]
                ]
        else:
            fallback_id = _FALLBACK_BY_KIND.get(intent.kind, "generic-structured")
            selected = capabilities[fallback_id]
            selected_by = "carrier_fallback"
            selected_level = RoutingLevel.deterministic
            selected_confidence = 1.0 if intent.kind in _FALLBACK_BY_KIND else 0.5
            selected_reason = f"no specialized signal; use {selected.profile_id}"
            if advisory_matches:
                candidates = list(dict.fromkeys(item[1].profile_id for item in advisory_matches))
                best_score, best_capability, best_detector = advisory_matches[0]
                reminders.append(
                    ReviewReminder(
                        code=f"review_profile_suggested:{best_detector.detector_id}",
                        event=event_value,
                        severity=ReminderSeverity.warning,
                        message=(
                            f"材料可能属于 {best_capability.profile_id!r}：{best_detector.reason}。"
                            "当前保持通用 renderer；请补结构化 profile/引用后再启用专用审阅。"
                        ),
                        field_path="review_context.profile_id",
                        suggested_profile_id=best_capability.profile_id,
                    )
                )
                selected_level = RoutingLevel.suggestion
                selected_confidence = max(0.0, 1.0 - best_score)

    assert selected is not None
    schema_id = explicit.schema_id if explicit and explicit.schema_id else intent.data_schema_id

    allowed_relations = {
        item.value if isinstance(item, ReferenceRelation) else str(item)
        for item in selected.reference_relations
    }
    unsupported_relations = sorted({
        item.relation.value if isinstance(item.relation, ReferenceRelation) else str(item.relation)
        for item in references
        if allowed_relations
        and (
            item.relation.value
            if isinstance(item.relation, ReferenceRelation)
            else str(item.relation)
        )
        not in allowed_relations
    })
    if unsupported_relations:
        reminders.append(
            ReviewReminder(
                code="review_reference_relation_unsupported",
                event=event_value,
                severity=ReminderSeverity.blocking,
                message=(
                    f"profile {selected.profile_id!r} 不接受引用关系 "
                    f"{', '.join(unsupported_relations)}；允许值：{', '.join(sorted(allowed_relations))}。"
                ),
                field_path="review_context.references[].relation",
            )
        )

    reminders.extend(
        _embedded_review_consistency_reminders(
            intent,
            event_value,
            references,
        )
    )

    if _capability_event_enabled(selected, event_value):
        for requirement in selected.context_requirements:
            if any(
                _field_present(intent, path, references, requirement.minimum_count)
                for path in requirement.any_of
            ):
                continue
            reminders.append(
                ReviewReminder(
                    code=requirement.code,
                    event=event_value,
                    severity=requirement.severity,
                    message=requirement.message,
                    field_path=" | ".join(requirement.any_of),
                )
            )
        status = selected.status.value if isinstance(selected.status, CapabilityStatus) else str(selected.status)
        if status != CapabilityStatus.available.value:
            reminders.append(
                ReviewReminder(
                    code=f"review_profile_{status}",
                    event=event_value,
                    severity=ReminderSeverity.info,
                    message=(
                        f"profile {selected.profile_id!r} 当前状态为 {status}；"
                        f"专用能力未完整时使用 {selected.fallback_for(intent.kind) or 'carrier'} fallback。"
                    ),
                    field_path="review_context.profile_id",
                )
            )

    # Resolution/reminders are server-owned derived fields.  An incoming
    # review_context may declare profile/schema/references, but cannot preserve
    # stale blockers or manufacture a successful resolution trace.
    reminder_by_code: dict[str, ReviewReminder] = {}
    for item in reminders:
        reminder_by_code[item.code] = item
    final_reminders = tuple(reminder_by_code.values())

    context = ReviewContext(
        profile_id=selected.profile_id,
        profile_version=selected.version,
        schema_id=schema_id,
        references=references,
        resolution=ReviewResolutionTrace(
            selected_by=selected_by,
            routing_level=selected_level,
            confidence=selected_confidence,
            reason=selected_reason,
            candidates=tuple(candidates),
        ),
        reminders=final_reminders,
    )
    return ReviewSubmissionResolution(context=context, capability=selected)


def reminders_for_output(context: ReviewContext) -> list[dict[str, Any]]:
    """Return only actionable reminders; generic successful routing stays quiet."""

    return [
        item.to_dict()
        for item in context.reminders
        if (
            item.severity.value
            if isinstance(item.severity, ReminderSeverity)
            else str(item.severity)
        )
        != ReminderSeverity.info.value
    ]


__all__ = [
    "CATALOG_VERSION",
    "REVIEW_FACILITY_HANDBOOK",
    "CapabilityStatus",
    "ContextRequirement",
    "DetectorSpec",
    "ReferenceRelation",
    "ReminderEvent",
    "ReminderSeverity",
    "ReviewCapability",
    "ReviewContext",
    "ReviewReference",
    "ReviewReminder",
    "ReviewResolutionTrace",
    "ReviewSubmissionIntent",
    "ReviewSubmissionResolution",
    "capability_catalog",
    "reminders_for_output",
    "resolve_review_submission",
    "review_capabilities",
    "review_capability_map",
    "validate_review_capabilities",
]
