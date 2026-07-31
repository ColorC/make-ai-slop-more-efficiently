"""Applicability checks and refresh triggers for time-sensitive game guides."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import EnvironmentScopeV1, EvidenceReferenceV1, GuideKnowledgeV1


GuideUseMode = Literal["actionable", "discovery_only", "rejected"]
GuideRefreshTrigger = Literal[
    "first_entry_to_new_system",
    "high_value_hard_to_reverse_choice",
    "version_or_season_change",
    "two_consecutive_failures",
    "knowledge_expired",
]


class GuideDecisionContextV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str = Field(min_length=1)
    build_scope_id: str = Field(min_length=1)
    account_scope_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    decision_at: datetime
    game_version: str | None = Field(default=None, min_length=1)
    season: str | None = Field(default=None, min_length=1)
    server_stage: str | None = Field(default=None, min_length=1)

    @field_validator("decision_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("guide decision time must include a timezone")
        return value


class GuideAssessmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guide_id: str
    mode: GuideUseMode
    reasons: list[str] = Field(min_length=1)


def assess_guide(
    guide: GuideKnowledgeV1,
    context: GuideDecisionContextV1,
    environment: EnvironmentScopeV1,
) -> GuideAssessmentV1:
    """Decide how a guide may influence the next game action.

    A guide can become actionable only when version, season, and server stage are
    all observed and match the source applicability. Discovery-only records may
    suggest interfaces or questions, but cannot decide a hard-to-reverse action.
    """

    canonical_mismatches: list[str] = []
    if context.environment_id != environment.id:
        canonical_mismatches.append("decision_environment_mismatch")
    if context.build_scope_id != environment.build_scope_id:
        canonical_mismatches.append("decision_build_scope_mismatch")
    if context.account_scope_id != environment.account_scope_id:
        canonical_mismatches.append("decision_account_scope_mismatch")
    if context.channel != environment.channel:
        canonical_mismatches.append("decision_channel_mismatch")
    if canonical_mismatches:
        return GuideAssessmentV1(
            guide_id=guide.id,
            mode="rejected",
            reasons=canonical_mismatches,
        )
    if guide.environment_id != environment.id:
        return GuideAssessmentV1(
            guide_id=guide.id,
            mode="rejected",
            reasons=["guide_environment_mismatch"],
        )
    if guide.status == "contradicted":
        return GuideAssessmentV1(
            guide_id=guide.id,
            mode="rejected",
            reasons=["live_evidence_contradicts_source"],
        )
    if guide.status == "stale":
        return GuideAssessmentV1(
            guide_id=guide.id,
            mode="discovery_only",
            reasons=["source_is_stale"],
        )
    if guide.status == "unverified":
        return GuideAssessmentV1(
            guide_id=guide.id,
            mode="discovery_only",
            reasons=["source_applicability_is_unverified"],
        )
    if guide.fresh_until is None or context.decision_at >= guide.fresh_until:
        return GuideAssessmentV1(
            guide_id=guide.id,
            mode="discovery_only",
            reasons=["source_freshness_window_expired"],
        )

    context_values = (context.game_version, context.season, context.server_stage)
    if any(value is None for value in context_values):
        return GuideAssessmentV1(
            guide_id=guide.id,
            mode="discovery_only",
            reasons=["live_environment_identity_is_incomplete"],
        )

    mismatches: list[str] = []
    if guide.applicable_build_scope_id != context.build_scope_id:
        mismatches.append("build_scope_mismatch")
    if guide.applicable_account_scope_id != context.account_scope_id:
        mismatches.append("account_scope_mismatch")
    if guide.applicable_channel != context.channel:
        mismatches.append("channel_mismatch")
    if guide.applicable_game_version != context.game_version:
        mismatches.append("game_version_mismatch")
    if guide.season != context.season:
        mismatches.append("season_mismatch")
    if guide.server_stage != context.server_stage:
        mismatches.append("server_stage_mismatch")
    if mismatches:
        return GuideAssessmentV1(guide_id=guide.id, mode="rejected", reasons=mismatches)

    return GuideAssessmentV1(
        guide_id=guide.id,
        mode="actionable",
        reasons=["live_environment_matches_source_applicability"],
    )


def should_refresh_guides(
    trigger: GuideRefreshTrigger,
    *,
    consecutive_failures: int = 0,
) -> bool:
    if trigger == "two_consecutive_failures":
        return consecutive_failures >= 2
    return True


def actionable_guides(
    guides: list[GuideKnowledgeV1],
    context: GuideDecisionContextV1,
    environment: EnvironmentScopeV1,
) -> list[GuideKnowledgeV1]:
    return [
        guide
        for guide in guides
        if assess_guide(guide, context, environment).mode == "actionable"
    ]


def load_guide_seed(
    path: Path,
    *,
    environment_id: str,
    research_record_id: str,
) -> list[GuideKnowledgeV1]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "sanguo-guide-seed.v1":
        raise ValueError("unsupported guide seed schema")
    if payload.get("research_record_id") != research_record_id:
        raise ValueError("guide seed research record id does not match the requested source")
    records = []
    for item in payload.get("guides", []):
        record_payload = dict(item)
        record_payload.setdefault("created_at", payload["retrieved_at"])
        records.append(
            GuideKnowledgeV1(
                **record_payload,
                environment_id=environment_id,
                evidence_refs=[
                    EvidenceReferenceV1(
                        environment_id=environment_id,
                        source_ids=[research_record_id],
                    )
                ],
            )
        )
    if len({record.id for record in records}) != len(records):
        raise ValueError("guide seed ids must be unique")
    return records
