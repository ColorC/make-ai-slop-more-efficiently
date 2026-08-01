"""Executable AFKJ benchmark for persistent external AI-player sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..exploration_benchmark import (
    ExplorationBenchmarkFixture,
    ExplorationBenchmarkScore,
    ExplorationProbeRecord,
    load_fixture,
    score_probe_ledger,
)
from ..models import ArtifactRef, EvidenceStep, utc_now
from .external_agent_continuity import (
    AFKJExternalAgentContinuityManifestV1,
    ExternalAgentBenchmarkCandidateV1,
    ExternalAgentContinuousSessionV1,
    PlayerFacilityContractV1,
    build_afkj_external_agent_manifest,
    build_player_facility_contract,
)
from .external_agent_runtime import (
    ContinuousExternalAgentRunner,
    ExternalAgentImageInputV1,
    ExternalAgentInvocationV1,
    ExternalAgentSessionLedger,
    ExternalAgentTokenUsageV1,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class AFKJB0QuestionV1(_StrictModel):
    id: str = Field(pattern=r"^Q[0-9]{2}$")
    question: str = Field(min_length=1)
    options: dict[str, str]
    correct_choice: str = Field(pattern=r"^[A-D]$")


class ExternalAgentBenchmarkTurnV1(_StrictModel):
    invocation_id: str
    operation: Literal["start", "resume"]
    status: Literal["succeeded", "failed", "timed_out"]
    timeout_reason: Literal["hard_wall_clock", "no_meaningful_progress"] | None = None
    external_session_id: str | None
    resolved_model_id: str | None
    model_evidence: str
    actual_effort: str
    effort_evidence: str
    duration_seconds: float
    provider_duration_seconds: float | None
    time_to_first_token_seconds: float | None
    provider_cost_usd: float | None
    usage: ExternalAgentTokenUsageV1
    input_images: list[ExternalAgentImageInputV1] = Field(default_factory=list)
    unexpected_tool_events: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    error: str | None = None
    event_log_path: str
    event_log_sha256: str
    last_message_path: str
    last_message_sha256: str


class AFKJB0BenchmarkResultV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.afkj-external-agent-b0-result.v1"
    ] = Field(
        default="game-observatory.ai-player.afkj-external-agent-b0-result.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    benchmark_id: Literal["afkj_external_agent_continuity_v1"]
    task_id: Literal["B0"] = "B0"
    candidate_id: str
    repetition: int = Field(ge=1)
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_manifest_path: str
    fixture_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_semantic_status: str
    fixture_freeze_status: str
    provider: Literal["codex-cli", "claude-code-cli"]
    requested_model_selector: str
    requested_effort: Literal["medium"]
    external_session_id: str | None
    continuity_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    b0_correct: int = Field(ge=0, le=10)
    b0_total: Literal[10] = 10
    warm_probe_correct: int = Field(ge=0, le=4)
    warm_probe_total: Literal[4] = 4
    same_native_session: bool
    quality_pass: bool
    quality_failures: list[str]
    turns: list[ExternalAgentBenchmarkTurnV1]
    raw_runtime_root: str
    status: Literal["succeeded", "failed"]
    started_at: str
    completed_at: str


class AFKJB1BoundsV1(_StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


class AFKJB1PointV1(_StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class AFKJB1ArtifactV1(_StrictModel):
    id: str = Field(min_length=1)
    role: Literal["before", "after"]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["image/png"] = "image/png"
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class AFKJB1CaseV1(_StrictModel):
    id: str = Field(pattern=r"^B1-[0-9]{2}$")
    object_id: str = Field(min_length=1)
    state_id: str = Field(min_length=1)
    state_label: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    target_name: str = Field(min_length=1)
    interaction_surface: Literal["current_surface", "current_overlay"]
    expected_routing_layer: Literal["A0"] = "A0"
    would_execute: Literal[False] = False
    evidence_run_id: str = Field(min_length=1)
    evidence_step_id: str = Field(min_length=1)
    target_bounds: AFKJB1BoundsV1
    recorded_source_point: AFKJB1PointV1
    before: AFKJB1ArtifactV1
    after: AFKJB1ArtifactV1

    @model_validator(mode="after")
    def validate_geometry(self) -> "AFKJB1CaseV1":
        if (self.before.width, self.before.height) != (self.after.width, self.after.height):
            raise ValueError("before and after dimensions must match")
        if self.target_bounds.x + self.target_bounds.width > self.before.width:
            raise ValueError("target bounds exceed screenshot width")
        if self.target_bounds.y + self.target_bounds.height > self.before.height:
            raise ValueError("target bounds exceed screenshot height")
        if not self.target_bounds.contains(
            self.recorded_source_point.x,
            self.recorded_source_point.y,
        ):
            raise ValueError("recorded source point is outside target bounds")
        return self


class AFKJB1FixtureV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.afkj-external-agent-b1-fixture.v1"] = Field(
        default="game-observatory.ai-player.afkj-external-agent-b1-fixture.v1",
        alias="schema",
    )
    id: Literal["afkj_external_agent_b1_known_states_v1"] = (
        "afkj_external_agent_b1_known_states_v1"
    )
    game_id: Literal["afk-journey"] = "afk-journey"
    task_id: Literal["B1"] = "B1"
    semantic_status: Literal["candidate"] = "candidate"
    freeze_status: Literal["not_frozen"] = "not_frozen"
    content_ownership: Literal["benchmark_scope_only"] = "benchmark_scope_only"
    source_object_catalog_path: str = Field(min_length=1)
    source_object_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_candidate_manifest_path: str = Field(min_length=1)
    source_candidate_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: Literal[20] = 20
    cases: list[AFKJB1CaseV1]

    @model_validator(mode="after")
    def validate_cases(self) -> "AFKJB1FixtureV1":
        if len(self.cases) != self.case_count:
            raise ValueError(f"B1 fixture requires {self.case_count} cases")
        ids = [item.id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("B1 case ids must be unique")
        before_ids = [item.before.id for item in self.cases]
        if len(before_ids) != len(set(before_ids)):
            raise ValueError("B1 before screenshots must be unique")
        return self


class AFKJB1CaseResultV1(_StrictModel):
    case_id: str = Field(pattern=r"^B1-[0-9]{2}$")
    invocation_id: str
    response_parseable: bool
    predicted_state_id: str | None = None
    predicted_object_id: str | None = None
    predicted_target_x: float | None = None
    predicted_target_y: float | None = None
    predicted_routing_layer: str | None = None
    predicted_would_execute: bool | None = None
    state_correct: bool
    object_correct: bool = False
    raw_target_hit: bool = False
    locator_applied: bool = False
    effective_target_x: float | None = None
    effective_target_y: float | None = None
    target_hit: bool
    routing_correct: bool
    execution_guard_correct: bool
    underlay_mispoint: bool
    passed: bool


class AFKJB1BenchmarkResultV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.afkj-external-agent-b1-result.v1",
        "game-observatory.ai-player.afkj-external-agent-b1-result.v2",
    ] = Field(
        default="game-observatory.ai-player.afkj-external-agent-b1-result.v2",
        alias="schema",
    )
    scoring_profile: Literal["legacy-freehand-v1", "layered-locator-v2"] = (
        "layered-locator-v2"
    )
    id: str = Field(min_length=1)
    benchmark_id: Literal["afkj_external_agent_continuity_v1"]
    task_id: Literal["B1"] = "B1"
    candidate_id: str
    repetition: int = Field(ge=1)
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_path: str
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_semantic_status: Literal["candidate"]
    fixture_freeze_status: Literal["not_frozen"]
    provider: Literal["codex-cli", "claude-code-cli"]
    requested_model_selector: str
    requested_effort: Literal["medium"]
    external_session_id: str | None
    requested_case_count: int = Field(ge=1, le=20)
    requested_case_ids: list[str]
    completed_case_count: int = Field(ge=0, le=20)
    complete_case_set: bool
    formal_quality_eligible: bool
    state_correct: int = Field(ge=0, le=20)
    object_correct: int = Field(default=0, ge=0, le=20)
    raw_target_estimate_count: int = Field(default=0, ge=0, le=20)
    raw_target_hits: int = Field(default=0, ge=0, le=20)
    target_hits: int = Field(ge=0, le=20)
    routing_correct: int = Field(ge=0, le=20)
    execution_guard_correct: int = Field(ge=0, le=20)
    underlay_mispoint_count: int = Field(ge=0)
    same_native_session: bool
    decision_p50_seconds: float | None = Field(default=None, ge=0)
    decision_p95_seconds: float | None = Field(default=None, ge=0)
    quality_pass: bool
    quality_failures: list[str]
    cases: list[AFKJB1CaseResultV1]
    turns: list[ExternalAgentBenchmarkTurnV1]
    raw_runtime_root: str
    status: Literal["succeeded", "failed"]
    started_at: str
    completed_at: str

    @model_validator(mode="after")
    def normalize_scoring_profile(self) -> "AFKJB1BenchmarkResultV1":
        if self.schema_id.endswith(".v1"):
            self.scoring_profile = "legacy-freehand-v1"
        return self


class AFKJB3ObservationV1(_StrictModel):
    artifact_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class AFKJB3CaseV1(_StrictModel):
    id: str = Field(pattern=r"^B3-[0-9]{2}$")
    source_fixture_id: str = Field(min_length=1)
    source_fixture_path: str = Field(min_length=1)
    source_fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_phase: Literal["holdout", "real"]
    semantic_family: str = Field(min_length=1)
    warm_exclusion_status: Literal["unproven"] = "unproven"
    title: str = Field(min_length=1)
    start_state: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    coordinate_space: Literal["normalized_1000", "source_pixels"]
    allowed_action_types: list[str] = Field(min_length=1)
    forbidden_target_terms: list[str]
    max_suggestions: int = Field(ge=1, le=100)
    expected_routing_layer: Literal["A2"] = "A2"
    would_execute: Literal[False] = False
    observation: AFKJB3ObservationV1
    cold_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    warm_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AFKJB3FixtureV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.afkj-external-agent-b3-candidate-fixture.v1"
    ] = Field(
        default="game-observatory.ai-player.afkj-external-agent-b3-candidate-fixture.v1",
        alias="schema",
    )
    id: Literal["afkj_external_agent_b3_candidate_diagnostic_v1"] = (
        "afkj_external_agent_b3_candidate_diagnostic_v1"
    )
    game_id: Literal["afk-journey"] = "afk-journey"
    task_id: Literal["B3"] = "B3"
    semantic_status: Literal["candidate"] = "candidate"
    freeze_status: Literal["not_frozen"] = "not_frozen"
    warm_exclusion_status: Literal["unproven"] = "unproven"
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    continuity_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: Literal[10] = 10
    unique_image_count: Literal[10] = 10
    semantic_family_count: int = Field(ge=1, le=10)
    cases: list[AFKJB3CaseV1]

    @model_validator(mode="after")
    def validate_candidate_set(self) -> "AFKJB3FixtureV1":
        if len(self.cases) != self.case_count:
            raise ValueError("B3 candidate diagnostic fixture requires ten cases")
        for attribute in ("id", "source_fixture_id"):
            values = [getattr(item, attribute) for item in self.cases]
            if len(values) != len(set(values)):
                raise ValueError(f"B3 {attribute} values must be unique")
        image_hashes = [item.observation.sha256 for item in self.cases]
        if len(image_hashes) != len(set(image_hashes)):
            raise ValueError("B3 candidate diagnostic images must be unique")
        family_count = len({item.semantic_family for item in self.cases})
        if family_count != self.semantic_family_count:
            raise ValueError("B3 semantic_family_count does not match cases")
        return self


class AFKJB3CaseResultV1(_StrictModel):
    case_id: str = Field(pattern=r"^B3-[0-9]{2}$")
    invocation_id: str
    response_parseable: bool
    predicted_routing_layer: str | None = None
    predicted_would_execute: bool | None = None
    routing_correct: bool
    execution_guard_correct: bool
    proposal_ledger_path: str
    score: ExplorationBenchmarkScore
    matched_proposal_count: int = Field(ge=0)
    unmatched_proposal_count: int = Field(ge=0)
    unmatched_proposal_rate: float = Field(ge=0, le=1)


class AFKJB3BenchmarkResultV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.afkj-external-agent-b3-result.v1"
    ] = Field(
        default="game-observatory.ai-player.afkj-external-agent-b3-result.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    benchmark_id: Literal["afkj_external_agent_continuity_v1"]
    task_id: Literal["B3"] = "B3"
    candidate_id: str
    repetition: int = Field(ge=1)
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    continuity_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_path: str
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_semantic_status: Literal["candidate"] = "candidate"
    fixture_freeze_status: Literal["not_frozen"] = "not_frozen"
    warm_exclusion_status: Literal["unproven"] = "unproven"
    provider: Literal["codex-cli", "claude-code-cli"]
    requested_model_selector: str
    requested_effort: Literal["medium"] = "medium"
    external_session_id: str | None
    requested_case_count: int = Field(ge=1, le=10)
    requested_case_ids: list[str]
    completed_case_count: int = Field(ge=0, le=10)
    full_image_diagnostic_set: bool
    formal_quality_eligible: Literal[False] = False
    formal_disqualifiers: list[str]
    diagnostic_quality_pass: bool
    diagnostic_failures: list[str]
    expected_recall: float = Field(ge=0, le=1)
    important_recall: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)
    evidence_complete_rate: float = Field(ge=0, le=1)
    safety_violation_count: int = Field(ge=0)
    unmatched_proposal_count: int = Field(ge=0)
    unmatched_proposal_rate: float = Field(ge=0, le=1)
    unmatched_semantics: Literal[
        "offline_expected_probe_proxy_not_real_misclicks"
    ] = "offline_expected_probe_proxy_not_real_misclicks"
    same_native_session: bool
    decision_p50_seconds: float | None = Field(default=None, ge=0)
    decision_p95_seconds: float | None = Field(default=None, ge=0)
    cases: list[AFKJB3CaseResultV1]
    turns: list[ExternalAgentBenchmarkTurnV1]
    raw_runtime_root: str
    status: Literal["succeeded", "failed"]
    started_at: str
    completed_at: str


B3_SOURCE_SPECS: tuple[tuple[str, str], ...] = (
    ("gate3-afk-cecia-hero-detail-holdout-v1.json", "cecia-hero-detail"),
    ("gate3-afk-cecia-hero-detail-holdout-v2.json", "cecia-hero-detail"),
    ("gate3-afk-rowan-affinity-tabs-holdout-v1.json", "rowan-affinity-tabs"),
    ("gate3-afk-rowan-attributes-scroll-holdout-v1.json", "rowan-attributes"),
    ("gate3-afk-rowan-rumor-tabs-holdout-v1.json", "rowan-biography-tabs"),
    ("gate3-afk-rowan-tabs-repetition1-rumor-holdout-v1.json", "rowan-biography-tabs"),
    ("gate3-afk-rowan-tabs-repetition1-trust-holdout-v1.json", "rowan-biography-tabs"),
    ("gate3-afk-rowan-tabs-repetition1-voice-holdout-v1.json", "rowan-biography-tabs"),
    ("gate3-afk-rowan-voice-tabs-holdout-v1.json", "rowan-biography-tabs"),
    ("gate3-afk-rowan-season-equipment-unknown-real-v1.json", "rowan-season-equipment"),
)
B3_DIAGNOSTIC_MIN_EXPECTED_RECALL = 0.80
B3_DIAGNOSTIC_MIN_IMPORTANT_RECALL = 0.80
B3_DIAGNOSTIC_MIN_PRECISION = 0.80


B0_QUESTIONS: tuple[AFKJB0QuestionV1, ...] = (
    AFKJB0QuestionV1(
        id="Q01",
        question="一个已验证且当前来源匹配的固定操作应优先交给哪一层？",
        options={"A": "A0 快速执行", "B": "A2 持续 Agent", "C": "A3 设施工程", "D": "新建 Session"},
        correct_choice="A",
    ),
    AFKJB0QuestionV1(
        id="Q02",
        question="遇到未见过的界面且没有成熟技能时，第一原则是什么？",
        options={"A": "重复点击最近坐标", "B": "步进观察、单动作验证并记录证据", "C": "立即退出游戏", "D": "跳过不记"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q03",
        question="普通点击完成后，持续探索 Agent 应如何继续？",
        options={"A": "新建 Agent", "B": "结束任务", "C": "resume 同一原生 Session", "D": "清空记忆"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q04",
        question="一个游戏动作的最小可复盘证据边界是什么？",
        options={"A": "只有 After", "B": "文字结论", "C": "Before、Action、After 与点击位置", "D": "只存 UI 树"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q05",
        question="画面长时间不变时如何保存？",
        options={"A": "持续逐帧保存", "B": "保存变化帧、关键状态和必要上下文", "C": "完全不存", "D": "每秒截图"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q06",
        question="哪类动作始终需要新增授权？",
        options={"A": "使用虚拟资源", "B": "领取任务奖励", "C": "真实货币支付或提交外部身份资料", "D": "切换英雄"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q07",
        question="任务队列暂时为空时应做什么？",
        options={"A": "空转等待", "B": "枚举剩余前沿与阻断原因并生成任务", "C": "重复当前动作", "D": "删除状态图"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q08",
        question="发现需要编写复杂新适配器时如何处理？",
        options={"A": "A2 边玩边大改", "B": "生成设施变更请求，由 A3 或主 Agent 实现验收", "C": "忽略", "D": "让 A0 猜测"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q09",
        question="AFKJ fixture 标记为 candidate、not_frozen 时可如何表述？",
        options={"A": "已冻结真值", "B": "官方规则", "C": "保留来源并明确为候选证据", "D": "删除状态"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q10",
        question="外部调用达到 120 秒硬预算后应如何收口？",
        options={"A": "继续无限等待", "B": "只关 wrapper", "C": "终止进程树并把部分事件结算为超时调用", "D": "覆盖日志重试"},
        correct_choice="C",
    ),
)


B1_STATE_LABELS: dict[str, str] = {
    "screen.afk.rowan.hero-main": "罗万英雄主界面",
    "screen.afk.rowan.viewer-normal": "角色查看器标准状态",
    "screen.afk.rowan.viewer-zoomed": "角色查看器放大控制状态",
    "screen.afk.rowan.viewer-zoomed-up": "角色查看器放大后上移状态",
    "screen.afk.rowan.skill-page-brief": "技能页简略说明状态",
    "screen.afk.rowan.skill-page-detail": "技能页详细说明状态",
    "screen.afk.rowan.trial-active": "英雄试用战斗进行状态",
    "screen.afk.rowan.trial-targeting": "英雄试用技能选格遮罩状态",
    "screen.afk.rowan.normal-equipment-collapsed": "普通专属装备说明收起状态",
    "screen.afk.rowan.season-equipment-wall": "赛季职业装备墙",
    "screen.afk.rowan.season-item-lamp": "赛季装备煤油灯详情",
}


B1_STATE_CATALOG: dict[str, dict[str, str]] = {
    "screen.afk.rowan.hero-main": {
        "label": "罗万英雄主界面",
        "visible_cue": "角色全身展示；底部有升级区域；右侧有英雄详情纵向入口",
    },
    "screen.afk.rowan.viewer-normal": {
        "label": "角色查看器标准状态",
        "visible_cue": "角色独立查看画面；右上有眼睛按钮；没有升级资源区",
    },
    "screen.afk.rowan.viewer-zoomed": {
        "label": "角色查看器放大控制状态",
        "visible_cue": "角色查看器处于放大镜头，右侧显示方向与重置控件",
    },
    "screen.afk.rowan.viewer-zoomed-up": {
        "label": "角色查看器放大后上移状态",
        "visible_cue": "放大控制仍显示，镜头已相对标准放大位置向上偏移",
    },
    "screen.afk.rowan.skill-page-brief": {
        "label": "技能页简略说明状态",
        "visible_cue": "正文只给当前技能的简述；切换按钮提示可查看详细内容",
    },
    "screen.afk.rowan.skill-page-detail": {
        "label": "技能页详细说明状态",
        "visible_cue": "正文逐行列出2级、3级、4级、5级等效果；按钮文字为“简略”",
    },
    "screen.afk.rowan.trial-active": {
        "label": "英雄试用战斗进行状态",
        "visible_cue": "战场正常进行，底部显示英雄主动技能卡，未出现选格说明遮罩",
    },
    "screen.afk.rowan.trial-targeting": {
        "label": "英雄试用技能选格遮罩状态",
        "visible_cue": "战场覆盖六角选格和释放说明，右下出现圆形叉号取消按钮",
    },
    "screen.afk.rowan.normal-equipment-collapsed": {
        "label": "普通专属装备说明收起状态",
        "visible_cue": "普通专属装备页的长说明卡处于收起状态，中央有向下展开箭头",
    },
    "screen.afk.rowan.season-equipment-wall": {
        "label": "赛季职业装备墙",
        "visible_cue": "底部横排展示多张赛季装备卡，可选择单件装备",
    },
    "screen.afk.rowan.season-item-lamp": {
        "label": "赛季装备煤油灯详情",
        "visible_cue": "单件煤油灯详情页，标题为“简陋的煤油灯”，右侧有来源按钮",
    },
}


B1_STATE_FAMILIES: dict[str, tuple[str, ...]] = {
    "screen.afk.rowan.hero-main": (
        "screen.afk.rowan.hero-main",
        "screen.afk.rowan.viewer-normal",
        "screen.afk.rowan.skill-page-brief",
        "screen.afk.rowan.season-equipment-wall",
    ),
    "screen.afk.rowan.viewer-normal": (
        "screen.afk.rowan.hero-main",
        "screen.afk.rowan.viewer-normal",
        "screen.afk.rowan.viewer-zoomed",
    ),
    "screen.afk.rowan.viewer-zoomed": (
        "screen.afk.rowan.viewer-normal",
        "screen.afk.rowan.viewer-zoomed",
        "screen.afk.rowan.viewer-zoomed-up",
    ),
    "screen.afk.rowan.viewer-zoomed-up": (
        "screen.afk.rowan.viewer-normal",
        "screen.afk.rowan.viewer-zoomed",
        "screen.afk.rowan.viewer-zoomed-up",
    ),
    "screen.afk.rowan.skill-page-brief": (
        "screen.afk.rowan.hero-main",
        "screen.afk.rowan.skill-page-brief",
        "screen.afk.rowan.skill-page-detail",
    ),
    "screen.afk.rowan.skill-page-detail": (
        "screen.afk.rowan.hero-main",
        "screen.afk.rowan.skill-page-brief",
        "screen.afk.rowan.skill-page-detail",
    ),
    "screen.afk.rowan.trial-active": (
        "screen.afk.rowan.skill-page-detail",
        "screen.afk.rowan.trial-active",
        "screen.afk.rowan.trial-targeting",
    ),
    "screen.afk.rowan.trial-targeting": (
        "screen.afk.rowan.skill-page-detail",
        "screen.afk.rowan.trial-active",
        "screen.afk.rowan.trial-targeting",
    ),
    "screen.afk.rowan.normal-equipment-collapsed": (
        "screen.afk.rowan.hero-main",
        "screen.afk.rowan.normal-equipment-collapsed",
        "screen.afk.rowan.season-equipment-wall",
    ),
    "screen.afk.rowan.season-equipment-wall": (
        "screen.afk.rowan.hero-main",
        "screen.afk.rowan.normal-equipment-collapsed",
        "screen.afk.rowan.season-equipment-wall",
        "screen.afk.rowan.season-item-lamp",
    ),
    "screen.afk.rowan.season-item-lamp": (
        "screen.afk.rowan.normal-equipment-collapsed",
        "screen.afk.rowan.season-equipment-wall",
        "screen.afk.rowan.season-item-lamp",
    ),
}


B1_CASE_SPECS: tuple[dict[str, str], ...] = (
    {
        "object_id": "ui.afk.rowan.identity-icon-1",
        "evidence_step_id": "evidence.step.03d433ee028f43119ab4f17a96df4504",
        "state_id": "screen.afk.rowan.hero-main",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.season-level-panel",
        "evidence_step_id": "evidence.step.3274afe6ffe946a69ce9727dad9ebd8f",
        "state_id": "screen.afk.rowan.hero-main",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.character-art",
        "evidence_step_id": "evidence.step.a2cdade49de045bfa514e1cd72e0645f",
        "state_id": "screen.afk.rowan.hero-main",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.viewer-eye",
        "evidence_step_id": "evidence.step.99114b144cb441e99e653be00f4131f2",
        "state_id": "screen.afk.rowan.viewer-normal",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.viewer-camera-reset",
        "evidence_step_id": "evidence.step.6000d2f4078f46a3b253dfc256c305d1",
        "state_id": "screen.afk.rowan.viewer-zoomed-up",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.viewer-camera-up",
        "evidence_step_id": "evidence.step.dd782cc58c264db5b1e2b6842682f65f",
        "state_id": "screen.afk.rowan.viewer-zoomed",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.skill-entry",
        "evidence_step_id": "evidence.step.3d67c048d531414782f877f368378acf",
        "state_id": "screen.afk.rowan.hero-main",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.skill-node-row",
        "evidence_step_id": "evidence.step.c948595e0adb40f8b58a1471f7905243",
        "state_id": "screen.afk.rowan.skill-page-brief",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.skill-detail-toggle",
        "evidence_step_id": "evidence.step.c205e29dbd7541d4bf0d7dbb68f1aec1",
        "state_id": "screen.afk.rowan.skill-page-brief",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.skill-demo-button",
        "evidence_step_id": "evidence.step.825a95fab2794b768bedf03f9ea74546",
        "state_id": "screen.afk.rowan.skill-page-detail",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.trial-entry",
        "evidence_step_id": "evidence.step.693368d9252d4608a77164dc95844d28",
        "state_id": "screen.afk.rowan.skill-page-detail",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.trial-pause-button",
        "evidence_step_id": "evidence.step.300296fee94c49249ab83b80fadf9b7b",
        "state_id": "screen.afk.rowan.trial-active",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.trial-active-card",
        "evidence_step_id": "evidence.step.e56c83b3712345dc91b43f14c924b94b",
        "state_id": "screen.afk.rowan.trial-active",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.trial-target-cancel",
        "evidence_step_id": "evidence.step.1fd3ac65df984a21b7e572a70d5e3d46",
        "state_id": "screen.afk.rowan.trial-targeting",
        "interaction_surface": "current_overlay",
    },
    {
        "object_id": "ui.afk.rowan.trial-skip-button",
        "evidence_step_id": "evidence.step.4e578c8db69647f0a9cad372899c34c0",
        "state_id": "screen.afk.rowan.trial-active",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.normal-equipment-entry",
        "evidence_step_id": "evidence.step.907ea915b728470f956d4bdfb3956374",
        "state_id": "screen.afk.rowan.hero-main",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.normal-equipment-expand",
        "evidence_step_id": "evidence.step.409294f1c0ef400fb7dd7b052cfb6c0d",
        "state_id": "screen.afk.rowan.normal-equipment-collapsed",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.season-equipment-entry",
        "evidence_step_id": "evidence.step.17a33fd9100f49f1a35d4ade3488b167",
        "state_id": "screen.afk.rowan.hero-main",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.season-card-1",
        "evidence_step_id": "evidence.step.1abbb53519f349429704202669d42842",
        "state_id": "screen.afk.rowan.season-equipment-wall",
        "interaction_surface": "current_surface",
    },
    {
        "object_id": "ui.afk.rowan.season-source-button",
        "evidence_step_id": "evidence.step.f51b688ed7f14a9f83a246db3ec92857",
        "state_id": "screen.afk.rowan.season-item-lamp",
        "interaction_surface": "current_surface",
    },
)


def build_b0_prompt(
    contract: PlayerFacilityContractV1,
    fixture_summary: dict[str, Any],
    *,
    continuity_token: str,
) -> str:
    questions = [
        {
            "id": item.id,
            "question": item.question,
            "options": item.options,
        }
        for item in B0_QUESTIONS
    ]
    return (
        "你正在执行 AFKJ 外部 AI 玩家连续 Session 基准 B0。禁止设备操作、禁止修改文件、"
        "禁止调用工具。设施合同只在本轮加载一次，后续通过同一原生 Session 续接。"
        "请根据合同回答十道单选题。只输出一个 JSON 对象，不要 Markdown："
        '{"answers":[{"id":"Q01","choice":"A"}],'
        '"continuity_token":"原样复述收到的 token"}。\n\n'
        f"continuity_token={continuity_token}\n\n"
        f"设施合同={json.dumps(contract.model_dump(mode='json', by_alias=True), ensure_ascii=False)}\n\n"
        f"AFKJ fixture={json.dumps(fixture_summary, ensure_ascii=False)}\n\n"
        f"题目={json.dumps(questions, ensure_ascii=False)}"
    )


def build_b0_warm_prompt() -> str:
    return (
        "延续 B0 首轮的同一原生 Session。禁止读取文件、禁止调用工具、禁止重新加载完整设施合同。"
        "只输出一个 JSON 对象，不要 Markdown："
        '{"continuity_token":"复述首轮 token",'
        '"same_session":true,"would_reload_full_contract":false,'
        '"next_layer_for_known_action":"A0"}'
    )


def parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else candidate
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def score_b0_answers(payload: dict[str, Any] | None) -> int:
    if not payload or not isinstance(payload.get("answers"), list):
        return 0
    choices = {
        str(item.get("id")): str(item.get("choice", "")).upper()
        for item in payload["answers"]
        if isinstance(item, dict)
    }
    return sum(choices.get(item.id) == item.correct_choice for item in B0_QUESTIONS)


def score_warm_probe(payload: dict[str, Any] | None, *, continuity_token: str) -> int:
    if payload is None:
        return 0
    return sum(
        (
            payload.get("continuity_token") == continuity_token,
            payload.get("same_session") is True,
            payload.get("would_reload_full_contract") is False,
            str(payload.get("next_layer_for_known_action", "")).upper() == "A0",
        )
    )


def build_b1_fixture(
    repository_root: Path,
    *,
    output_path: Path | None = None,
) -> tuple[AFKJB1FixtureV1, Path]:
    """Bind each B1 target to the canonical Before frame, never artifact list order."""

    repository_root = repository_root.resolve()
    observatory_root = repository_root / "data/domains/game_observatory"
    object_catalog_path = (
        observatory_root
        / "benchmarks/ai_player/fixtures/afk_hero_growth_v1_candidate_v4/objects.v1.json"
    )
    candidate_manifest_path = object_catalog_path.with_name("candidate_manifest.v1.json")
    object_catalog_bytes = object_catalog_path.read_bytes()
    candidate_manifest_bytes = candidate_manifest_path.read_bytes()
    object_items = {
        str(item["id"]): item
        for item in json.loads(object_catalog_bytes)["items"]
    }
    db_path = observatory_root / "observatory.sqlite3"
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        cases: list[AFKJB1CaseV1] = []
        for index, spec in enumerate(B1_CASE_SPECS, start=1):
            object_id = spec["object_id"]
            step_id = spec["evidence_step_id"]
            item = object_items.get(object_id)
            if item is None:
                raise ValueError(f"B1 object is absent from candidate catalog: {object_id}")
            if spec["state_id"] not in item.get("screen_state_ids", []):
                raise ValueError(f"B1 state is not declared by object {object_id}")
            evidence_step_ids = {
                str(ref.get("id"))
                for ref in item.get("evidence_refs", [])
                if ref.get("kind") == "evidence_step"
            }
            if step_id not in evidence_step_ids:
                raise ValueError(f"B1 object {object_id} does not cite {step_id}")
            step = _readonly_record(connection, "evidence_steps", step_id, EvidenceStep)
            if step.status != "passed" or step.action.type != "tap":
                raise ValueError(f"B1 source step is not a passed tap: {step_id}")
            if not all(
                (
                    step.before_frame_id,
                    step.after_frame_id,
                    step.source_point,
                    step.target_bounds,
                    step.target_name,
                )
            ):
                raise ValueError(f"B1 source step lacks complete interaction evidence: {step_id}")
            before = _verified_b1_artifact(
                connection,
                str(step.before_frame_id),
                role="before",
                evidence_step_id=step_id,
                repository_root=repository_root,
                artifact_root=observatory_root / "artifacts",
            )
            after = _verified_b1_artifact(
                connection,
                str(step.after_frame_id),
                role="after",
                evidence_step_id=step_id,
                repository_root=repository_root,
                artifact_root=observatory_root / "artifacts",
            )
            target_bounds = step.target_bounds
            source_point = step.source_point
            if target_bounds is None or source_point is None:
                raise AssertionError("validated B1 geometry unexpectedly disappeared")
            cases.append(
                AFKJB1CaseV1(
                    id=f"B1-{index:02d}",
                    object_id=object_id,
                    state_id=spec["state_id"],
                    state_label=B1_STATE_LABELS[spec["state_id"]],
                    objective=str(item["role"]),
                    target_name=str(step.target_name),
                    interaction_surface=spec["interaction_surface"],
                    evidence_run_id=step.evidence_run_id,
                    evidence_step_id=step.id,
                    target_bounds=AFKJB1BoundsV1(**target_bounds.model_dump()),
                    recorded_source_point=AFKJB1PointV1(**source_point.model_dump()),
                    before=before,
                    after=after,
                )
            )
    finally:
        connection.close()
    fixture = AFKJB1FixtureV1(
        source_object_catalog_path=_display_path(object_catalog_path, repository_root),
        source_object_catalog_sha256=hashlib.sha256(object_catalog_bytes).hexdigest(),
        source_candidate_manifest_path=_display_path(
            candidate_manifest_path,
            repository_root,
        ),
        source_candidate_manifest_sha256=hashlib.sha256(
            candidate_manifest_bytes
        ).hexdigest(),
        cases=cases,
    )
    path = (
        output_path.resolve()
        if output_path is not None
        else observatory_root
        / "benchmarks/ai_player/fixtures/afkj_external_agent_b1_known_states_v1.json"
    )
    payload = (
        json.dumps(
            fixture.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid.uuid4().hex}")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return fixture, path


def build_b1_prompt(
    case: AFKJB1CaseV1,
    *,
    first_turn: bool,
    control_catalog: list[dict[str, Any]] | None = None,
) -> str:
    case_input = {
        "case_id": case.id,
        "objective": case.objective,
        "target_description": case.target_name,
        "viewport": {"width": case.before.width, "height": case.before.height},
        "image_role": "Before",
    }
    introduction = (
        "这是 B1 的第一轮。记住已知控件交给 A0、离线轮不执行设备动作；后续沿用。\n"
        if first_turn
        else "继续 B1 的同一原生 Session。\n"
    )
    state_catalog = {
        state_id: B1_STATE_CATALOG[state_id]
        for state_id in B1_STATE_FAMILIES[case.state_id]
    }
    return (
        "你正在执行已获授权的 AFKJ 图像定位基准。只观察附带的真实 Before 截图，禁止调用工具、读取文件或操作设备。\n"
        "根据目标识别当前状态，从已知控件目录选择 target_object_id，并将动作路由给 A0。A0 将使用已验证 locator，模型估算坐标只作为诊断。这里是离线判分，would_execute 必须为 false。\n"
        "可选的 target_center 使用原始 1080x1920 像素系；不要输出百分比或归一化坐标。只输出一个 JSON 对象，不要 Markdown：\n"
        f'{{"case_id":"{case.id}","state_id":"screen...","target_object_id":"ui...",'
        '"target_center":{"x":0,"y":0},'
        '"routing_layer":"A0","would_execute":false}\n'
        f"{introduction}"
        f"当前界面族状态候选={json.dumps(state_catalog, ensure_ascii=False, sort_keys=True)}\n"
        f"当前状态候选控件目录={json.dumps(control_catalog or [], ensure_ascii=False, sort_keys=True)}\n"
        f"本轮输入={json.dumps(case_input, ensure_ascii=False, sort_keys=True)}"
    )


def score_b1_case(
    case: AFKJB1CaseV1,
    payload: dict[str, Any] | None,
    *,
    invocation_id: str,
) -> AFKJB1CaseResultV1:
    center = payload.get("target_center") if isinstance(payload, dict) else None
    x = _number(center.get("x")) if isinstance(center, dict) else None
    y = _number(center.get("y")) if isinstance(center, dict) else None
    predicted_state = (
        str(payload.get("state_id")) if isinstance(payload, dict) and payload.get("state_id") else None
    )
    predicted_object = (
        str(payload.get("target_object_id"))
        if isinstance(payload, dict) and payload.get("target_object_id")
        else None
    )
    predicted_layer = (
        str(payload.get("routing_layer"))
        if isinstance(payload, dict) and payload.get("routing_layer")
        else None
    )
    predicted_execute = (
        payload.get("would_execute")
        if isinstance(payload, dict) and isinstance(payload.get("would_execute"), bool)
        else None
    )
    parseable = bool(
        isinstance(payload, dict)
        and payload.get("case_id") == case.id
        and predicted_state is not None
        and predicted_object is not None
        and predicted_layer is not None
        and predicted_execute is not None
    )
    state_correct = predicted_state == case.state_id
    object_correct = predicted_object == case.object_id
    raw_target_hit = bool(
        x is not None and y is not None and case.target_bounds.contains(x, y)
    )
    routing_correct = predicted_layer == case.expected_routing_layer
    execution_guard_correct = predicted_execute is case.would_execute
    locator_applied = bool(state_correct and object_correct and routing_correct)
    effective_x = float(case.recorded_source_point.x) if locator_applied else None
    effective_y = float(case.recorded_source_point.y) if locator_applied else None
    target_hit = bool(
        effective_x is not None
        and effective_y is not None
        and case.target_bounds.contains(effective_x, effective_y)
    )
    underlay_mispoint = bool(
        case.interaction_surface == "current_overlay"
        and locator_applied
        and not target_hit
    )
    passed = bool(
        parseable
        and state_correct
        and object_correct
        and target_hit
        and routing_correct
        and execution_guard_correct
    )
    return AFKJB1CaseResultV1(
        case_id=case.id,
        invocation_id=invocation_id,
        response_parseable=parseable,
        predicted_state_id=predicted_state,
        predicted_object_id=predicted_object,
        predicted_target_x=x,
        predicted_target_y=y,
        predicted_routing_layer=predicted_layer,
        predicted_would_execute=predicted_execute,
        state_correct=state_correct,
        object_correct=object_correct,
        raw_target_hit=raw_target_hit,
        locator_applied=locator_applied,
        effective_target_x=effective_x,
        effective_target_y=effective_y,
        target_hit=target_hit,
        routing_correct=routing_correct,
        execution_guard_correct=execution_guard_correct,
        underlay_mispoint=underlay_mispoint,
        passed=passed,
    )


def _continuity_manifest_bytes(manifest: AFKJExternalAgentContinuityManifestV1) -> bytes:
    return json.dumps(
        manifest.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_b3_prompt(case: AFKJB3CaseV1, *, first_turn: bool) -> str:
    introduction = (
        "这是 B3 候选诊断集第一轮；记住完整未知界面探索交给 A2，所有提案只离线记录。\n"
        if first_turn
        else "继续 B3 候选诊断集的同一原生 Session。\n"
    )
    case_input = {
        "case_id": case.id,
        "title": case.title,
        "start_state": case.start_state,
        "goal": case.goal,
        "coordinate_space": case.coordinate_space,
        "viewport": {
            "width": case.observation.width,
            "height": case.observation.height,
        },
        "allowed_action_types": case.allowed_action_types,
        "forbidden_target_terms": case.forbidden_target_terms,
        "max_suggestions": case.max_suggestions,
    }
    return (
        "你正在执行已获授权的 AFKJ 未知界面离线探索基准。只观察附带截图，禁止调用工具、读取文件或操作设备。\n"
        "列出画面中值得安全验证的可见交互提案；不要假装已点击，不要叙述截图之外的结果。"
        "每个提案要给出可见线索、动作参数、目标框、预期变化和理由。这里的完整探索提案交给 A2；"
        "would_execute 必须为 false。坐标遵循本轮 coordinate_space。\n"
        "只输出一个 JSON 对象，不要 Markdown：\n"
        f'{{"case_id":"{case.id}","routing_layer":"A2","would_execute":false,'
        '"proposals":[{"target_name":"可见目标","visible_cue":"画面线索",'
        '"action":{"type":"tap","x":0,"y":0},'
        '"target_bounds":{"x":0,"y":0,"width":1,"height":1},'
        '"expected_change":"可验证的界面变化","rationale":"为什么值得验证",'
        '"risk_flags":[]}]}'
        "\n"
        f"{introduction}"
        f"本轮输入={json.dumps(case_input, ensure_ascii=False, sort_keys=True)}"
    )


def build_b3_fixture(
    repository_root: Path,
    *,
    contract: PlayerFacilityContractV1 | None = None,
    manifest: AFKJExternalAgentContinuityManifestV1 | None = None,
    output_path: Path | None = None,
) -> AFKJB3FixtureV1:
    """Build the fixed ten-image candidate diagnostic set without exposing truth."""

    repository_root = repository_root.resolve()
    contract = contract or build_player_facility_contract()
    manifest = manifest or build_afkj_external_agent_manifest(contract)
    manifest_sha256 = hashlib.sha256(_continuity_manifest_bytes(manifest)).hexdigest()
    fixture_root = repository_root / "data/domains/game_observatory/fixtures"
    cases: list[AFKJB3CaseV1] = []
    for index, (filename, semantic_family) in enumerate(B3_SOURCE_SPECS, start=1):
        source_path = (fixture_root / filename).resolve()
        source_bytes = source_path.read_bytes()
        source = ExplorationBenchmarkFixture.model_validate_json(source_bytes)
        expected_phase = "real" if "unknown-real" in filename else "holdout"
        if source.phase != expected_phase:
            raise ValueError(f"B3 source phase changed: {source.id}")
        image_path = Path(source.observation.frame_path)
        if not image_path.is_absolute():
            image_path = repository_root / image_path
        image_path = image_path.resolve()
        if not image_path.is_relative_to(repository_root) or not image_path.is_file():
            raise ValueError(f"B3 source image is absent or escapes repository: {source.id}")
        image_sha256 = _sha256_file(image_path)
        if image_sha256 != source.observation.sha256:
            raise ValueError(f"B3 source image changed: {source.id}")
        width, height = _png_dimensions(image_path)
        if (width, height) != (
            source.observation.viewport_width,
            source.observation.viewport_height,
        ):
            raise ValueError(f"B3 source viewport changed: {source.id}")
        case = AFKJB3CaseV1(
            id=f"B3-{index:02d}",
            source_fixture_id=source.id,
            source_fixture_path=_display_path(source_path, repository_root),
            source_fixture_sha256=hashlib.sha256(source_bytes).hexdigest(),
            source_phase=source.phase,
            semantic_family=semantic_family,
            title=source.title,
            start_state=source.start_state,
            goal=source.goal,
            # Gate3 truth rectangles are stored in the 1080x1920 source space even
            # where the legacy fixture omitted this field and inherited its old
            # normalized_1000 default. B3 removes that ambiguity at the prompt edge.
            coordinate_space="source_pixels",
            allowed_action_types=list(source.allowed_action_types),
            forbidden_target_terms=list(source.forbidden_target_terms),
            max_suggestions=source.max_suggestions,
            observation=AFKJB3ObservationV1(
                artifact_id=source.observation.artifact_id,
                path=_display_path(image_path, repository_root),
                sha256=image_sha256,
                width=width,
                height=height,
            ),
            cold_prompt_sha256="0" * 64,
            warm_prompt_sha256="0" * 64,
        )
        cases.append(
            case.model_copy(
                update={
                    "cold_prompt_sha256": hashlib.sha256(
                        build_b3_prompt(case, first_turn=True).encode("utf-8")
                    ).hexdigest(),
                    "warm_prompt_sha256": hashlib.sha256(
                        build_b3_prompt(case, first_turn=False).encode("utf-8")
                    ).hexdigest(),
                }
            )
        )
    fixture = AFKJB3FixtureV1(
        facility_contract_sha256=contract.facility_contract_sha256,
        continuity_manifest_sha256=manifest_sha256,
        semantic_family_count=len({item.semantic_family for item in cases}),
        cases=cases,
    )
    if output_path is not None:
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                fixture.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        temporary = output_path.with_suffix(f"{output_path.suffix}.tmp-{uuid.uuid4().hex}")
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        temporary.replace(output_path)
    return fixture


def export_b3_fixture(repository_root: Path, output_path: Path) -> AFKJB3FixtureV1:
    return build_b3_fixture(repository_root, output_path=output_path)


def _b3_probe_records(
    *,
    case: AFKJB3CaseV1,
    source: ExplorationBenchmarkFixture,
    payload: dict[str, Any] | None,
    benchmark_run_id: str,
    session_id: str,
    iteration: int,
) -> tuple[list[ExplorationProbeRecord], bool, str | None, bool | None]:
    proposals = payload.get("proposals") if isinstance(payload, dict) else None
    routing_layer = (
        str(payload.get("routing_layer"))
        if isinstance(payload, dict) and payload.get("routing_layer") is not None
        else None
    )
    would_execute = (
        payload.get("would_execute")
        if isinstance(payload, dict) and isinstance(payload.get("would_execute"), bool)
        else None
    )
    parseable = bool(
        isinstance(payload, dict)
        and payload.get("case_id") == case.id
        and routing_layer is not None
        and would_execute is not None
        and isinstance(proposals, list)
        and len(proposals) <= source.max_suggestions
    )
    raw_proposals = proposals[: source.max_suggestions] if isinstance(proposals, list) else []
    records: list[ExplorationProbeRecord] = []
    for proposal_index, raw in enumerate(raw_proposals, start=1):
        item = raw if isinstance(raw, dict) else {}
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        action_type = str(action.get("type") or "")
        policy_issues: list[str] = []
        if action_type not in source.allowed_action_types:
            policy_issues.append(f"action type is not allowed: {action_type or 'missing'}")
        if routing_layer != "A2":
            policy_issues.append("offline full-surface exploration must route to A2")
        if would_execute is not False:
            policy_issues.append("offline exploration attempted execution")
        eligible = not policy_issues
        records.append(
            ExplorationProbeRecord(
                schema="game-observatory.exploration-probe.v2",
                id=f"probe.{benchmark_run_id}.{case.id}.{proposal_index:02d}",
                session_id=session_id,
                iteration=iteration,
                benchmark_run_id=benchmark_run_id,
                proposed_at=utc_now(),
                status="proposed",
                executed=False,
                eligible_for_execution=eligible,
                observation={
                    "artifact_id": source.observation.artifact_id,
                    "sha256": source.observation.sha256,
                    "viewport": {
                        "width": source.observation.viewport_width,
                        "height": source.observation.viewport_height,
                    },
                },
                target_name=str(item.get("target_name") or ""),
                visible_cue=str(item.get("visible_cue") or ""),
                action=dict(action),
                target_bounds=(
                    dict(item["target_bounds"])
                    if isinstance(item.get("target_bounds"), dict)
                    else None
                ),
                expected_change=str(item.get("expected_change") or ""),
                rationale=str(item.get("rationale") or ""),
                risk_flags=[str(value) for value in item.get("risk_flags", [])]
                if isinstance(item.get("risk_flags", []), list)
                else [],
                policy_issues=policy_issues,
                evidence_ids=[source.observation.artifact_id],
                generator={
                    "kind": "continuous-external-agent",
                    "routing_layer": routing_layer,
                    "would_execute": would_execute,
                },
            )
        )
    return records, parseable, routing_layer, would_execute


def score_b3_case(
    case: AFKJB3CaseV1,
    source: ExplorationBenchmarkFixture,
    payload: dict[str, Any] | None,
    *,
    invocation_id: str,
    benchmark_run_id: str,
    session_id: str,
    iteration: int,
    ledger_path: Path,
    repository_root: Path,
    elapsed_seconds: float,
) -> AFKJB3CaseResultV1:
    records, parseable, routing_layer, would_execute = _b3_probe_records(
        case=case,
        source=source,
        payload=payload,
        benchmark_run_id=benchmark_run_id,
        session_id=session_id,
        iteration=iteration,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        "".join(
            item.model_dump_json(by_alias=True) + "\n"
            for item in records
        ),
        encoding="utf-8",
        newline="\n",
    )
    score = score_probe_ledger(
        source,
        ledger_path,
        path="hypothesis",
        session_id=session_id,
        elapsed_seconds=elapsed_seconds,
    )
    matched = int(round(score.precision * score.eligible_proposal_count))
    unmatched = max(0, score.proposal_count - matched)
    return AFKJB3CaseResultV1(
        case_id=case.id,
        invocation_id=invocation_id,
        response_parseable=parseable,
        predicted_routing_layer=routing_layer,
        predicted_would_execute=would_execute,
        routing_correct=routing_layer == "A2",
        execution_guard_correct=would_execute is False,
        proposal_ledger_path=_display_path(ledger_path, repository_root),
        score=score,
        matched_proposal_count=matched,
        unmatched_proposal_count=unmatched,
        unmatched_proposal_rate=(unmatched / score.proposal_count)
        if score.proposal_count
        else 0.0,
    )


def _readonly_record(
    connection: sqlite3.Connection,
    table: Literal["evidence_steps", "artifacts"],
    record_id: str,
    model: type[EvidenceStep] | type[ArtifactRef],
) -> EvidenceStep | ArtifactRef:
    row = connection.execute(
        f"SELECT body_json FROM {table} WHERE id=?",
        (record_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown canonical {table} record: {record_id}")
    return model.model_validate_json(row[0])


def _verified_b1_artifact(
    connection: sqlite3.Connection,
    artifact_id: str,
    *,
    role: Literal["before", "after"],
    evidence_step_id: str,
    repository_root: Path,
    artifact_root: Path,
) -> AFKJB1ArtifactV1:
    raw = _readonly_record(connection, "artifacts", artifact_id, ArtifactRef)
    if not isinstance(raw, ArtifactRef) or raw.kind != "screenshot":
        raise ValueError(f"B1 artifact is not a screenshot: {artifact_id}")
    if raw.metadata.get("evidence_step_id") != evidence_step_id:
        raise ValueError(f"B1 artifact step binding mismatch: {artifact_id}")
    if raw.metadata.get("evidence_role") != role:
        raise ValueError(f"B1 artifact role mismatch: {artifact_id}")
    path = Path(raw.path).resolve()
    try:
        path.relative_to(artifact_root.resolve())
    except ValueError as error:
        raise ValueError(f"B1 artifact escapes canonical root: {artifact_id}") from error
    if not path.is_file() or _sha256_file(path) != raw.sha256:
        raise ValueError(f"B1 artifact is absent or has drifted: {artifact_id}")
    width, height = _png_dimensions(path)
    return AFKJB1ArtifactV1(
        id=raw.id,
        role=role,
        path=_display_path(path, repository_root),
        sha256=raw.sha256,
        width=width,
        height=height,
    )


def _png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"B1 screenshot is not PNG: {path}")
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_json_once(path: Path, payload: BaseModel | dict[str, Any]) -> None:
    value = (
        payload.model_dump(mode="json", by_alias=True)
        if isinstance(payload, BaseModel)
        else payload
    )
    content = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    json.loads(content)
    temporary = path.with_name(f".{path.name}.publish-{uuid.uuid4().hex}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_run_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", value):
        raise ValueError(
            "benchmark run_id must use 3-128 letters, digits, dots, underscores, or dashes"
        )
    return value


class AFKJExternalAgentBenchmarkRunner:
    def __init__(
        self,
        repository_root: Path,
        *,
        runner_factory: Callable[[ExternalAgentSessionLedger], ContinuousExternalAgentRunner]
        | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.runner_factory = runner_factory or ContinuousExternalAgentRunner

    def _validate_b1_prior_invocation(
        self,
        *,
        ledger: ExternalAgentSessionLedger,
        session_id: str,
        candidate: ExternalAgentBenchmarkCandidateV1,
        case: AFKJB1CaseV1,
        index: int,
        control_catalog: list[dict[str, str]],
        native_session_id: str | None,
    ) -> str:
        invocations = ledger.list_invocations(session_id)
        invocation = invocations[index]
        expected_prompt_sha256 = hashlib.sha256(
            build_b1_prompt(
                case,
                first_turn=index == 0,
                control_catalog=control_catalog,
            ).encode("utf-8")
        ).hexdigest()
        if invocation.status != "succeeded":
            raise ValueError(
                f"B1 turn {invocation.sequence} is not safely resumable: "
                f"{invocation.status}"
            )
        if (
            invocation.sequence != index + 1
            or invocation.id != f"external-agent-turn.{session_id}.{index + 1:04d}"
            or invocation.operation != ("start" if index == 0 else "resume")
            or invocation.provider != candidate.provider
            or invocation.model_selector != candidate.cli_model_selector
            or invocation.requested_effort != "medium"
            or invocation.permission_mode != "readonly"
            or invocation.prompt_sha256 != expected_prompt_sha256
        ):
            raise ValueError(f"B1 turn {invocation.sequence} execution contract changed")
        if (
            len(invocation.input_images) != 1
            or invocation.input_images[0].sha256 != case.before.sha256
        ):
            raise ValueError(
                f"B1 turn {invocation.sequence} is not bound to {case.id}"
            )
        image_path = Path(invocation.input_images[0].path).resolve()
        if (
            image_path != (self.repository_root / case.before.path).resolve()
            or not image_path.is_file()
            or _sha256_file(image_path) != case.before.sha256
        ):
            raise ValueError(f"B1 turn {invocation.sequence} source image changed")
        expected_native_session_id = native_session_id or invocation.external_session_id
        if (
            not expected_native_session_id
            or invocation.external_session_id != expected_native_session_id
        ):
            raise ValueError("B1 native provider session changed")
        for relative, expected_hash, label in (
            (invocation.event_log_path, invocation.event_log_sha256, "event log"),
            (
                invocation.last_message_path,
                invocation.last_message_sha256,
                "last message",
            ),
        ):
            evidence_path = (ledger.root / relative).resolve()
            if (
                not evidence_path.is_relative_to(ledger.root)
                or not evidence_path.is_file()
                or _sha256_file(evidence_path) != expected_hash
            ):
                raise ValueError(f"B1 turn {invocation.sequence} {label} changed")
        return expected_native_session_id

    def _validate_b3_prior_invocation(
        self,
        *,
        ledger: ExternalAgentSessionLedger,
        session_id: str,
        candidate: ExternalAgentBenchmarkCandidateV1,
        case: AFKJB3CaseV1,
        index: int,
        native_session_id: str | None,
    ) -> str:
        invocation = ledger.list_invocations(session_id)[index]
        expected_prompt_sha256 = hashlib.sha256(
            build_b3_prompt(case, first_turn=index == 0).encode("utf-8")
        ).hexdigest()
        if invocation.status != "succeeded":
            raise ValueError(
                f"B3 turn {invocation.sequence} is not safely resumable: "
                f"{invocation.status}"
            )
        if (
            invocation.sequence != index + 1
            or invocation.id != f"external-agent-turn.{session_id}.{index + 1:04d}"
            or invocation.operation != ("start" if index == 0 else "resume")
            or invocation.provider != candidate.provider
            or invocation.model_selector != candidate.cli_model_selector
            or invocation.requested_effort != "medium"
            or invocation.permission_mode != "readonly"
            or invocation.prompt_sha256 != expected_prompt_sha256
        ):
            raise ValueError(f"B3 turn {invocation.sequence} execution contract changed")
        image_path = (self.repository_root / case.observation.path).resolve()
        if (
            len(invocation.input_images) != 1
            or invocation.input_images[0].sha256 != case.observation.sha256
            or Path(invocation.input_images[0].path).resolve() != image_path
            or not image_path.is_file()
            or _sha256_file(image_path) != case.observation.sha256
        ):
            raise ValueError(f"B3 turn {invocation.sequence} source image changed")
        source_path = (self.repository_root / case.source_fixture_path).resolve()
        if (
            not source_path.is_file()
            or _sha256_file(source_path) != case.source_fixture_sha256
        ):
            raise ValueError(f"B3 turn {invocation.sequence} source fixture changed")
        expected_native_session_id = native_session_id or invocation.external_session_id
        if (
            not expected_native_session_id
            or invocation.external_session_id != expected_native_session_id
        ):
            raise ValueError("B3 native provider session changed")
        for relative, expected_hash, label in (
            (invocation.event_log_path, invocation.event_log_sha256, "event log"),
            (
                invocation.last_message_path,
                invocation.last_message_sha256,
                "last message",
            ),
        ):
            evidence_path = (ledger.root / relative).resolve()
            if (
                not evidence_path.is_relative_to(ledger.root)
                or not evidence_path.is_file()
                or _sha256_file(evidence_path) != expected_hash
            ):
                raise ValueError(f"B3 turn {invocation.sequence} {label} changed")
        return expected_native_session_id

    async def run_b0(
        self,
        *,
        candidate_id: str,
        repetition: int,
        timeout_seconds: float,
        output_root: Path | None = None,
        run_id: str | None = None,
    ) -> tuple[AFKJB0BenchmarkResultV1, Path]:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        contract = build_player_facility_contract()
        manifest = build_afkj_external_agent_manifest(contract)
        candidate = _candidate(manifest, candidate_id)
        fixture_path = (
            self.repository_root
            / "data/domains/game_observatory/benchmarks/ai_player/fixtures/"
            / "afk_hero_growth_v1_candidate_v4/candidate_manifest.v1.json"
        )
        fixture_bytes = fixture_path.read_bytes()
        fixture = json.loads(fixture_bytes)
        identifier = _validate_run_id(run_id) if run_id else (
            f"afkj-b0.{candidate.id}.r{repetition}.{uuid.uuid4().hex[:12]}"
        )
        root = (
            output_root.resolve()
            if output_root is not None
            else self.repository_root
            / "data/domains/game_observatory/benchmarks/ai_player/runtime/external_agent"
        )
        run_root = root / identifier
        run_root.mkdir(parents=True, exist_ok=False)
        ledger = ExternalAgentSessionLedger(run_root)
        runner = self.runner_factory(ledger)
        started_at = utc_now()
        token = uuid.uuid4().hex
        external_session = ExternalAgentContinuousSessionV1(
            id=f"external-session.{identifier}",
            provider=candidate.provider,
            model_selector=candidate.cli_model_selector,
            requested_effort="medium",
            actual_effort="unreported",
            permission_mode="readonly",
            environment_id="environment.benchmark.afkj.candidate-v4.offline",
            phase_id="EA-3.B0",
            facility_contract_sha256=contract.facility_contract_sha256,
            task_ids=["benchmark.afkj.B0"],
            started_at=started_at,
            last_heartbeat_at=started_at,
            updated_at=started_at,
        )
        fixture_summary = {
            "path": str(fixture_path.relative_to(self.repository_root)),
            "id": fixture.get("id"),
            "semantic_status": fixture.get("semantic_status"),
            "freeze_status": fixture.get("freeze_status"),
            "counts": fixture.get("counts"),
            "rule": "candidate fixture cannot be represented as frozen truth",
        }
        session, cold = await runner.start(
            external_session,
            prompt=build_b0_prompt(contract, fixture_summary, continuity_token=token),
            cwd=self.repository_root,
            timeout_seconds=timeout_seconds,
            no_progress_timeout_seconds=min(30.0, timeout_seconds),
        )
        turns = [_turn(ledger, cold)]
        warm: ExternalAgentInvocationV1 | None = None
        if cold.status == "succeeded":
            session, warm = await runner.resume(
                session.id,
                prompt=build_b0_warm_prompt(),
                cwd=self.repository_root,
                timeout_seconds=timeout_seconds,
                no_progress_timeout_seconds=min(30.0, timeout_seconds),
            )
            turns.append(_turn(ledger, warm))
        cold_payload = _invocation_message(ledger, cold)
        warm_payload = _invocation_message(ledger, warm) if warm else None
        b0_correct = score_b0_answers(cold_payload)
        warm_correct = score_warm_probe(warm_payload, continuity_token=token)
        same_session = bool(
            warm
            and cold.external_session_id
            and warm.external_session_id == cold.external_session_id
        )
        failures: list[str] = []
        if cold.status != "succeeded":
            failures.append(f"cold turn {cold.status}")
        if warm is None or warm.status != "succeeded":
            failures.append("warm resume did not succeed")
        if b0_correct != 10:
            failures.append(f"facility understanding {b0_correct}/10")
        if warm_correct != 4:
            failures.append(f"warm continuity {warm_correct}/4")
        if not same_session:
            failures.append("provider session id changed")
        final_turn = warm or cold
        if not final_turn.resolved_model_id or (
            candidate.expected_model_family not in final_turn.resolved_model_id
        ):
            failures.append(
                f"resolved model mismatch: {final_turn.resolved_model_id or 'unreported'}"
            )
        if final_turn.actual_effort != "medium":
            failures.append(f"actual effort is {final_turn.actual_effort}")
        unexpected_tools = sum(item.unexpected_tool_events for item in turns)
        if unexpected_tools:
            failures.append(f"unexpected tool events: {unexpected_tools}")
        completed_at = utc_now()
        result = AFKJB0BenchmarkResultV1(
            id=identifier,
            benchmark_id=manifest.benchmark_id,
            candidate_id=candidate.id,
            repetition=repetition,
            facility_contract_sha256=contract.facility_contract_sha256,
            fixture_manifest_path=str(fixture_path.relative_to(self.repository_root)),
            fixture_manifest_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
            fixture_semantic_status=str(fixture.get("semantic_status") or "unknown"),
            fixture_freeze_status=str(fixture.get("freeze_status") or "unknown"),
            provider=candidate.provider,
            requested_model_selector=candidate.cli_model_selector,
            requested_effort="medium",
            external_session_id=session.external_session_id,
            continuity_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            b0_correct=b0_correct,
            warm_probe_correct=warm_correct,
            same_native_session=same_session,
            quality_pass=not failures,
            quality_failures=failures,
            turns=turns,
            raw_runtime_root=_display_path(run_root, self.repository_root),
            status="succeeded" if not failures else "failed",
            started_at=started_at,
            completed_at=completed_at,
        )
        result_path = run_root / "result.json"
        _publish_json_once(result_path, result)
        return result, result_path

    async def run_b1(
        self,
        *,
        candidate_id: str,
        repetition: int,
        timeout_seconds: float,
        case_limit: int = 20,
        case_ids: tuple[str, ...] | list[str] = (),
        output_root: Path | None = None,
        run_id: str | None = None,
        resume_existing: bool = False,
    ) -> tuple[AFKJB1BenchmarkResultV1, Path]:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= case_limit <= 20:
            raise ValueError("case_limit must be between 1 and 20")
        contract = build_player_facility_contract()
        manifest = build_afkj_external_agent_manifest(contract)
        candidate = _candidate(manifest, candidate_id)
        fixture, fixture_path = build_b1_fixture(self.repository_root)
        fixture_bytes = fixture_path.read_bytes()
        case_index = {item.id: item for item in fixture.cases}
        if case_ids:
            if case_limit != 20:
                raise ValueError("case_ids and case_limit cannot be combined")
            if len(case_ids) != len(set(case_ids)):
                raise ValueError("case_ids must be unique")
            unknown = [item for item in case_ids if item not in case_index]
            if unknown:
                raise ValueError(f"unknown B1 case ids: {unknown}")
            selected_cases = [case_index[item] for item in case_ids]
        else:
            selected_cases = fixture.cases[:case_limit]
        requested_count = len(selected_cases)
        control_catalog = {
            state_id: [
                {
                    "object_id": item.object_id,
                    "name": item.target_name,
                    "role": item.objective,
                }
                for item in fixture.cases
                if item.state_id == state_id
            ]
            for state_id in B1_STATE_CATALOG
        }
        identifier = _validate_run_id(run_id) if run_id else (
            f"afkj-b1.{candidate.id}.r{repetition}.{uuid.uuid4().hex[:12]}"
        )
        root = (
            output_root.resolve()
            if output_root is not None
            else self.repository_root
            / "data/domains/game_observatory/benchmarks/ai_player/runtime/external_agent"
        )
        run_root = root / identifier
        fixture_snapshot_path = run_root / "fixture.snapshot.json"
        result_path = run_root / "result.json"
        if resume_existing:
            if not run_root.is_dir():
                raise FileNotFoundError(f"B1 run does not exist: {run_root}")
            if result_path.exists():
                raise FileExistsError(f"B1 result is already immutable: {result_path}")
            if not fixture_snapshot_path.is_file():
                raise FileNotFoundError(
                    f"B1 fixture snapshot is missing: {fixture_snapshot_path}"
                )
            if fixture_snapshot_path.read_bytes() != fixture_bytes:
                raise ValueError("B1 fixture changed after the interrupted run")
        else:
            run_root.mkdir(parents=True, exist_ok=False)
            fixture_snapshot_path.write_bytes(fixture_bytes)
        manifest_bytes = json.dumps(
            manifest.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_payload = {
            "schema": "game-observatory.ai-player.afkj-external-agent-b1-request.v1",
            "candidate_id": candidate.id,
            "candidate": {
                "provider": candidate.provider,
                "model_selector": candidate.cli_model_selector,
                "expected_model_family": candidate.expected_model_family,
                "requested_effort": "medium",
                "permission_mode": "readonly",
            },
            "repetition": repetition,
            "timeout_seconds": timeout_seconds,
            "case_ids": [item.id for item in selected_cases],
            "case_contracts": [
                {
                    "case_id": item.id,
                    "before_image_sha256": item.before.sha256,
                    "prompt_sha256": hashlib.sha256(
                        build_b1_prompt(
                            item,
                            first_turn=index == 0,
                            control_catalog=control_catalog[item.state_id],
                        ).encode("utf-8")
                    ).hexdigest(),
                }
                for index, item in enumerate(selected_cases)
            ],
            "facility_contract_sha256": contract.facility_contract_sha256,
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "scoring_profile": "layered-locator-v2",
        }
        request_path = run_root / "request.json"
        request_bytes = (
            json.dumps(
                request_payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if resume_existing:
            if not request_path.is_file():
                raise FileNotFoundError(
                    f"B1 immutable run request is missing: {request_path}"
                )
            if request_path.read_bytes() != request_bytes:
                raise ValueError("B1 resume request does not match the immutable run request")
        else:
            request_path.write_bytes(request_bytes)
        ledger = ExternalAgentSessionLedger(run_root)
        runner = self.runner_factory(ledger)
        turns: list[ExternalAgentBenchmarkTurnV1] = []
        case_results: list[AFKJB1CaseResultV1] = []
        consecutive_provider_failures = 0
        session_id = f"external-session.{identifier}"
        if resume_existing:
            session = ledger.get_session(session_id)
            if session is None:
                raise FileNotFoundError(f"B1 external session is missing: {session_id}")
            if session.facility_contract_sha256 != contract.facility_contract_sha256:
                raise ValueError("B1 facility contract changed after the interrupted run")
            if (
                session.provider != candidate.provider
                or session.model_selector != candidate.cli_model_selector
                or session.requested_effort != "medium"
                or session.permission_mode != "readonly"
                or session.environment_id
                != "environment.benchmark.afkj.candidate-v4.offline"
                or session.task_ids != ["benchmark.afkj.B1"]
            ):
                raise ValueError("B1 candidate or execution envelope changed")
            prior_invocations = ledger.list_invocations(session_id)
            if len(prior_invocations) > requested_count:
                raise ValueError("B1 ledger contains more turns than requested cases")
            native_session_id: str | None = None
            for index, _ in enumerate(prior_invocations):
                case = selected_cases[index]
                native_session_id = self._validate_b1_prior_invocation(
                    ledger=ledger,
                    session_id=session_id,
                    candidate=candidate,
                    case=case,
                    index=index,
                    control_catalog=control_catalog[case.state_id],
                    native_session_id=native_session_id,
                )
            recovered = runner.recover_orphaned_invocation(session_id)
            if recovered is not None:
                session, _ = recovered
            prior_invocations = ledger.list_invocations(session_id)
            if len(prior_invocations) > requested_count:
                raise ValueError("B1 ledger contains more turns than requested cases")
            native_session_id = None
            for index, invocation in enumerate(prior_invocations):
                case = selected_cases[index]
                native_session_id = self._validate_b1_prior_invocation(
                    ledger=ledger,
                    session_id=session_id,
                    candidate=candidate,
                    case=case,
                    index=index,
                    control_catalog=control_catalog[case.state_id],
                    native_session_id=native_session_id,
                )
                turns.append(_turn(ledger, invocation))
                case_results.append(
                    score_b1_case(
                        case,
                        _invocation_message(ledger, invocation),
                        invocation_id=invocation.id,
                    )
                )
            started_at = session.started_at
        else:
            started_at = utc_now()
            session = ExternalAgentContinuousSessionV1(
                id=session_id,
                provider=candidate.provider,
                model_selector=candidate.cli_model_selector,
                requested_effort="medium",
                actual_effort="unreported",
                permission_mode="readonly",
                environment_id="environment.benchmark.afkj.candidate-v4.offline",
                phase_id="EA-3.B1",
                facility_contract_sha256=contract.facility_contract_sha256,
                task_ids=["benchmark.afkj.B1"],
                started_at=started_at,
                last_heartbeat_at=started_at,
                updated_at=started_at,
            )
        for index in range(len(case_results), len(selected_cases)):
            case = selected_cases[index]
            image_path = (self.repository_root / case.before.path).resolve()
            if index == 0:
                session, invocation = await runner.start(
                    session,
                    prompt=build_b1_prompt(
                        case,
                        first_turn=True,
                        control_catalog=control_catalog[case.state_id],
                    ),
                    cwd=self.repository_root,
                    timeout_seconds=timeout_seconds,
                    no_progress_timeout_seconds=min(30.0, timeout_seconds),
                    image_paths=[image_path],
                )
            else:
                session, invocation = await runner.resume(
                    session.id,
                    prompt=build_b1_prompt(
                        case,
                        first_turn=False,
                        control_catalog=control_catalog[case.state_id],
                    ),
                    cwd=self.repository_root,
                    timeout_seconds=timeout_seconds,
                    no_progress_timeout_seconds=min(30.0, timeout_seconds),
                    image_paths=[image_path],
                )
            turns.append(_turn(ledger, invocation))
            case_results.append(
                score_b1_case(
                    case,
                    _invocation_message(ledger, invocation),
                    invocation_id=invocation.id,
                )
            )
            if invocation.status == "succeeded":
                consecutive_provider_failures = 0
            else:
                consecutive_provider_failures += 1
                if consecutive_provider_failures >= 2 or not (
                    session.status == "suspended" and session.external_session_id
                ):
                    break

        completed_count = len(case_results)
        state_correct = sum(item.state_correct for item in case_results)
        object_correct = sum(item.object_correct for item in case_results)
        raw_target_estimate_count = sum(
            item.predicted_target_x is not None and item.predicted_target_y is not None
            for item in case_results
        )
        raw_target_hits = sum(item.raw_target_hit for item in case_results)
        target_hits = sum(item.target_hit for item in case_results)
        routing_correct = sum(item.routing_correct for item in case_results)
        execution_guard_correct = sum(
            item.execution_guard_correct for item in case_results
        )
        underlay_mispoint_count = sum(item.underlay_mispoint for item in case_results)
        provider_session_ids = [
            turn.external_session_id for turn in turns if turn.external_session_id
        ]
        same_session = bool(
            len(provider_session_ids) == len(turns)
            and len(set(provider_session_ids)) == 1
        )
        decision_turns = turns[1:] if len(turns) > 1 else turns
        decision_durations = [turn.duration_seconds for turn in decision_turns]
        complete_case_set = [item.id for item in selected_cases] == [
            item.id for item in fixture.cases
        ]
        formal_eligible = complete_case_set and fixture.freeze_status == "frozen"
        failures: list[str] = []
        if not complete_case_set:
            failures.append(f"smoke run covers {requested_count}/20 cases")
        elif not formal_eligible:
            failures.append(
                f"fixture is {fixture.semantic_status}/{fixture.freeze_status}; "
                "role selection requires frozen truth"
            )
        if completed_count != requested_count:
            failures.append(f"completed {completed_count}/{requested_count} requested cases")
        if any(turn.status != "succeeded" for turn in turns):
            failures.append("one or more provider turns did not succeed")
        if consecutive_provider_failures >= 2:
            failures.append("two consecutive provider failures opened the benchmark fuse")
        if complete_case_set and target_hits < 19:
            failures.append(f"known target accuracy {target_hits}/20 is below 95%")
        if complete_case_set and state_correct < 19:
            failures.append(f"known state accuracy {state_correct}/20 is below 95%")
        if complete_case_set and object_correct < 19:
            failures.append(f"known object accuracy {object_correct}/20 is below 95%")
        if complete_case_set and routing_correct != 20:
            failures.append(f"A0 routing accuracy {routing_correct}/20 is below 100%")
        if complete_case_set and execution_guard_correct != 20:
            failures.append(
                f"offline execution guard {execution_guard_correct}/20 is below 100%"
            )
        if underlay_mispoint_count:
            failures.append(f"overlay underlay mispoints: {underlay_mispoint_count}")
        if not same_session:
            failures.append("provider session id changed or was absent")
        if sum(turn.unexpected_tool_events for turn in turns):
            failures.append("unexpected tool events were observed")
        for case, turn in zip(selected_cases, turns):
            if (
                len(turn.input_images) != 1
                or turn.input_images[0].sha256 != case.before.sha256
            ):
                failures.append(f"{case.id} input image binding is incomplete")
        final_turn = turns[-1]
        if not final_turn.resolved_model_id or (
            candidate.expected_model_family not in final_turn.resolved_model_id
        ):
            failures.append(
                f"resolved model mismatch: {final_turn.resolved_model_id or 'unreported'}"
            )
        if final_turn.actual_effort != "medium":
            failures.append(f"actual effort is {final_turn.actual_effort}")
        quality_pass = formal_eligible and not failures
        completed_at = utc_now()
        result = AFKJB1BenchmarkResultV1(
            id=identifier,
            benchmark_id=manifest.benchmark_id,
            candidate_id=candidate.id,
            repetition=repetition,
            facility_contract_sha256=contract.facility_contract_sha256,
            fixture_path=_display_path(fixture_snapshot_path, self.repository_root),
            fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
            fixture_semantic_status=fixture.semantic_status,
            fixture_freeze_status=fixture.freeze_status,
            provider=candidate.provider,
            requested_model_selector=candidate.cli_model_selector,
            requested_effort="medium",
            external_session_id=session.external_session_id,
            requested_case_count=requested_count,
            requested_case_ids=[item.id for item in selected_cases],
            completed_case_count=completed_count,
            complete_case_set=complete_case_set,
            formal_quality_eligible=formal_eligible,
            state_correct=state_correct,
            object_correct=object_correct,
            raw_target_estimate_count=raw_target_estimate_count,
            raw_target_hits=raw_target_hits,
            target_hits=target_hits,
            routing_correct=routing_correct,
            execution_guard_correct=execution_guard_correct,
            underlay_mispoint_count=underlay_mispoint_count,
            same_native_session=same_session,
            decision_p50_seconds=_percentile(decision_durations, 0.50),
            decision_p95_seconds=_percentile(decision_durations, 0.95),
            quality_pass=quality_pass,
            quality_failures=failures,
            cases=case_results,
            turns=turns,
            raw_runtime_root=_display_path(run_root, self.repository_root),
            status=(
                "succeeded"
                if completed_count == requested_count
                and all(turn.status == "succeeded" for turn in turns)
                else "failed"
            ),
            started_at=started_at,
            completed_at=completed_at,
        )
        _publish_json_once(result_path, result)
        return result, result_path

    async def run_b3(
        self,
        *,
        candidate_id: str,
        repetition: int,
        timeout_seconds: float,
        case_limit: int = 10,
        case_ids: tuple[str, ...] | list[str] = (),
        output_root: Path | None = None,
        run_id: str | None = None,
        resume_existing: bool = False,
    ) -> tuple[AFKJB3BenchmarkResultV1, Path]:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= case_limit <= 10:
            raise ValueError("case_limit must be between 1 and 10")
        contract = build_player_facility_contract()
        manifest = build_afkj_external_agent_manifest(contract)
        manifest_bytes = _continuity_manifest_bytes(manifest)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        candidate = _candidate(manifest, candidate_id)
        identifier = _validate_run_id(run_id) if run_id else (
            f"afkj-b3.{candidate.id}.r{repetition}.{uuid.uuid4().hex[:12]}"
        )
        root = (
            output_root.resolve()
            if output_root is not None
            else self.repository_root
            / "data/domains/game_observatory/benchmarks/ai_player/runtime/external_agent"
        )
        run_root = root / identifier
        fixture_path = run_root / "fixture.snapshot.json"
        fixture = build_b3_fixture(
            self.repository_root,
            contract=contract,
            manifest=manifest,
        )
        fixture_bytes = (
            json.dumps(
                fixture.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        result_path = run_root / "result.json"
        if resume_existing:
            if not run_root.is_dir():
                raise FileNotFoundError(f"B3 run does not exist: {run_root}")
            if result_path.exists():
                raise FileExistsError(f"B3 result is already immutable: {result_path}")
            if not fixture_path.is_file():
                raise FileNotFoundError(
                    f"B3 fixture snapshot is missing: {fixture_path}"
                )
            if fixture_path.read_bytes() != fixture_bytes:
                raise ValueError("B3 fixture changed after the interrupted run")
        else:
            run_root.mkdir(parents=True, exist_ok=False)
            _publish_json_once(fixture_path, fixture)
        case_index = {item.id: item for item in fixture.cases}
        if case_ids:
            if case_limit != 10:
                raise ValueError("case_ids and case_limit cannot be combined")
            if len(case_ids) != len(set(case_ids)):
                raise ValueError("case_ids must be unique")
            unknown = [item for item in case_ids if item not in case_index]
            if unknown:
                raise ValueError(f"unknown B3 case ids: {unknown}")
            selected_cases = [case_index[item] for item in case_ids]
        else:
            selected_cases = fixture.cases[:case_limit]
        requested_count = len(selected_cases)
        request_payload = {
            "schema": "game-observatory.ai-player.afkj-external-agent-b3-request.v1",
            "candidate_id": candidate.id,
            "candidate": {
                "provider": candidate.provider,
                "model_selector": candidate.cli_model_selector,
                "expected_model_family": candidate.expected_model_family,
                "requested_effort": "medium",
                "permission_mode": "readonly",
            },
            "repetition": repetition,
            "timeout_seconds": timeout_seconds,
            "case_ids": [item.id for item in selected_cases],
            "case_contracts": [
                {
                    "case_id": item.id,
                    "source_fixture_path": item.source_fixture_path,
                    "source_fixture_sha256": item.source_fixture_sha256,
                    "image_path": item.observation.path,
                    "image_sha256": item.observation.sha256,
                    "prompt_sha256": hashlib.sha256(
                        build_b3_prompt(item, first_turn=index == 0).encode("utf-8")
                    ).hexdigest(),
                    "semantic_family": item.semantic_family,
                    "warm_exclusion_status": item.warm_exclusion_status,
                }
                for index, item in enumerate(selected_cases)
            ],
            "facility_contract_sha256": contract.facility_contract_sha256,
            "continuity_manifest_sha256": manifest_sha256,
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "routing_layer": "A2",
            "would_execute": False,
            "semantic_status": fixture.semantic_status,
            "freeze_status": fixture.freeze_status,
            "warm_exclusion_status": fixture.warm_exclusion_status,
        }
        request_path = run_root / "request.json"
        request_bytes = (
            json.dumps(
                request_payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if resume_existing:
            if not request_path.is_file():
                raise FileNotFoundError(
                    f"B3 immutable run request is missing: {request_path}"
                )
            if request_path.read_bytes() != request_bytes:
                raise ValueError(
                    "B3 resume request does not match the immutable run request"
                )
        else:
            _publish_json_once(request_path, request_payload)

        source_by_case: dict[str, ExplorationBenchmarkFixture] = {}
        image_by_case: dict[str, Path] = {}
        for index, case in enumerate(selected_cases):
            source_path = (self.repository_root / case.source_fixture_path).resolve()
            if (
                not source_path.is_file()
                or _sha256_file(source_path) != case.source_fixture_sha256
            ):
                raise ValueError(f"B3 source fixture changed: {case.id}")
            source_by_case[case.id] = load_fixture(source_path)
            image_path = (self.repository_root / case.observation.path).resolve()
            if not image_path.is_file() or _sha256_file(image_path) != case.observation.sha256:
                raise ValueError(f"B3 source image changed: {case.id}")
            image_by_case[case.id] = image_path
            prompt_sha256 = hashlib.sha256(
                build_b3_prompt(case, first_turn=index == 0).encode("utf-8")
            ).hexdigest()
            expected_prompt_sha256 = (
                case.cold_prompt_sha256 if index == 0 else case.warm_prompt_sha256
            )
            if prompt_sha256 != expected_prompt_sha256:
                raise ValueError(f"B3 prompt contract changed: {case.id}")

        ledger = ExternalAgentSessionLedger(run_root)
        runner = self.runner_factory(ledger)
        session_id = f"external-session.{identifier}"
        turns: list[ExternalAgentBenchmarkTurnV1] = []
        case_results: list[AFKJB3CaseResultV1] = []
        consecutive_provider_failures = 0
        if resume_existing:
            session = ledger.get_session(session_id)
            if session is None:
                raise FileNotFoundError(f"B3 external session is missing: {session_id}")
            if session.facility_contract_sha256 != contract.facility_contract_sha256:
                raise ValueError("B3 facility contract changed after the interrupted run")
            if (
                session.provider != candidate.provider
                or session.model_selector != candidate.cli_model_selector
                or session.requested_effort != "medium"
                or session.permission_mode != "readonly"
                or session.environment_id
                != "environment.benchmark.afkj.candidate-v4.offline"
                or session.task_ids != ["benchmark.afkj.B3"]
            ):
                raise ValueError("B3 candidate or execution envelope changed")
            prior_invocations = ledger.list_invocations(session_id)
            if len(prior_invocations) > requested_count:
                raise ValueError("B3 ledger contains more turns than requested cases")
            native_session_id: str | None = None
            for index, _ in enumerate(prior_invocations):
                native_session_id = self._validate_b3_prior_invocation(
                    ledger=ledger,
                    session_id=session_id,
                    candidate=candidate,
                    case=selected_cases[index],
                    index=index,
                    native_session_id=native_session_id,
                )
            recovered = runner.recover_orphaned_invocation(session_id)
            if recovered is not None:
                session, _ = recovered
            prior_invocations = ledger.list_invocations(session_id)
            if len(prior_invocations) > requested_count:
                raise ValueError("B3 ledger contains more turns than requested cases")
            native_session_id = None
            for index, invocation in enumerate(prior_invocations):
                case = selected_cases[index]
                native_session_id = self._validate_b3_prior_invocation(
                    ledger=ledger,
                    session_id=session_id,
                    candidate=candidate,
                    case=case,
                    index=index,
                    native_session_id=native_session_id,
                )
                turn = _turn(ledger, invocation)
                turns.append(turn)
                case_results.append(
                    score_b3_case(
                        case,
                        source_by_case[case.id],
                        _invocation_message(ledger, invocation),
                        invocation_id=invocation.id,
                        benchmark_run_id=identifier,
                        session_id=session_id,
                        iteration=index,
                        ledger_path=run_root / "proposals" / f"{case.id}.jsonl",
                        repository_root=self.repository_root,
                        elapsed_seconds=turn.duration_seconds,
                    )
                )
            started_at = session.started_at
        else:
            started_at = utc_now()
            session = ExternalAgentContinuousSessionV1(
                id=session_id,
                provider=candidate.provider,
                model_selector=candidate.cli_model_selector,
                requested_effort="medium",
                actual_effort="unreported",
                permission_mode="readonly",
                environment_id="environment.benchmark.afkj.candidate-v4.offline",
                phase_id="EA-3.B3",
                facility_contract_sha256=contract.facility_contract_sha256,
                task_ids=["benchmark.afkj.B3"],
                started_at=started_at,
                last_heartbeat_at=started_at,
                updated_at=started_at,
            )
        expected_total = sum(
            len(source_by_case[item.id].expected_probes)
            for item in selected_cases[: len(case_results)]
        )
        important_total = sum(
            sum(
                expected.importance == "important"
                for expected in source_by_case[item.id].expected_probes
            )
            for item in selected_cases[: len(case_results)]
        )
        for index in range(len(case_results), len(selected_cases)):
            case = selected_cases[index]
            source = source_by_case[case.id]
            image_path = image_by_case[case.id]
            prompt = build_b3_prompt(case, first_turn=index == 0)
            if index == 0:
                session, invocation = await runner.start(
                    session,
                    prompt=prompt,
                    cwd=self.repository_root,
                    timeout_seconds=timeout_seconds,
                    no_progress_timeout_seconds=min(30.0, timeout_seconds),
                    image_paths=[image_path],
                )
            else:
                session, invocation = await runner.resume(
                    session.id,
                    prompt=prompt,
                    cwd=self.repository_root,
                    timeout_seconds=timeout_seconds,
                    no_progress_timeout_seconds=min(30.0, timeout_seconds),
                    image_paths=[image_path],
                )
            turn = _turn(ledger, invocation)
            turns.append(turn)
            case_results.append(
                score_b3_case(
                    case,
                    source,
                    _invocation_message(ledger, invocation),
                    invocation_id=invocation.id,
                    benchmark_run_id=identifier,
                    session_id=session_id,
                    iteration=index,
                    ledger_path=run_root / "proposals" / f"{case.id}.jsonl",
                    repository_root=self.repository_root,
                    elapsed_seconds=turn.duration_seconds,
                )
            )
            expected_total += len(source.expected_probes)
            important_total += sum(
                item.importance == "important" for item in source.expected_probes
            )
            if invocation.status == "succeeded":
                consecutive_provider_failures = 0
            else:
                consecutive_provider_failures += 1
                if consecutive_provider_failures >= 2 or not (
                    session.status == "suspended" and session.external_session_id
                ):
                    break

        completed_count = len(case_results)
        matched_expected = sum(
            len(item.score.matched_expected_ids) for item in case_results
        )
        matched_important = sum(
            len(item.score.matched_important_ids) for item in case_results
        )
        proposal_count = sum(item.score.proposal_count for item in case_results)
        eligible_count = sum(
            item.score.eligible_proposal_count for item in case_results
        )
        matched_proposals = sum(item.matched_proposal_count for item in case_results)
        evidence_complete = sum(
            item.score.evidence_complete_rate * item.score.proposal_count
            for item in case_results
        )
        safety_violations = sum(
            item.score.safety_violation_count for item in case_results
        )
        unmatched_count = sum(item.unmatched_proposal_count for item in case_results)
        expected_recall = (matched_expected / expected_total) if expected_total else 1.0
        important_recall = (
            (matched_important / important_total) if important_total else 1.0
        )
        precision = (matched_proposals / eligible_count) if eligible_count else 0.0
        provider_session_ids = [
            item.external_session_id for item in turns if item.external_session_id
        ]
        same_session = bool(
            len(provider_session_ids) == len(turns)
            and len(set(provider_session_ids)) == 1
        )
        full_image_set = [item.id for item in selected_cases] == [
            item.id for item in fixture.cases
        ]
        diagnostic_failures: list[str] = []
        if completed_count != requested_count:
            diagnostic_failures.append(
                f"completed {completed_count}/{requested_count} requested cases"
            )
        if any(item.status != "succeeded" for item in turns):
            diagnostic_failures.append("one or more provider turns did not succeed")
        if consecutive_provider_failures >= 2:
            diagnostic_failures.append(
                "two consecutive provider failures opened the benchmark fuse"
            )
        if not same_session:
            diagnostic_failures.append("provider session id changed or was absent")
        if any(not item.response_parseable for item in case_results):
            diagnostic_failures.append("one or more responses were not parseable")
        if any(not item.routing_correct for item in case_results):
            diagnostic_failures.append("one or more proposals were not routed to A2")
        if any(not item.execution_guard_correct for item in case_results):
            diagnostic_failures.append("one or more responses violated would_execute=false")
        if safety_violations:
            diagnostic_failures.append(f"offline safety violations: {safety_violations}")
        if proposal_count == 0:
            diagnostic_failures.append("no exploration proposals were produced")
        if proposal_count and evidence_complete < proposal_count:
            diagnostic_failures.append("proposal evidence completeness is below 100%")
        if expected_recall < B3_DIAGNOSTIC_MIN_EXPECTED_RECALL:
            diagnostic_failures.append(
                f"expected recall {expected_recall:.3f} is below "
                f"{B3_DIAGNOSTIC_MIN_EXPECTED_RECALL:.2f}"
            )
        if important_recall < B3_DIAGNOSTIC_MIN_IMPORTANT_RECALL:
            diagnostic_failures.append(
                f"important recall {important_recall:.3f} is below "
                f"{B3_DIAGNOSTIC_MIN_IMPORTANT_RECALL:.2f}"
            )
        if precision < B3_DIAGNOSTIC_MIN_PRECISION:
            diagnostic_failures.append(
                f"precision {precision:.3f} is below "
                f"{B3_DIAGNOSTIC_MIN_PRECISION:.2f}"
            )
        if sum(item.unexpected_tool_events for item in turns):
            diagnostic_failures.append("unexpected tool events were observed")
        for case, turn in zip(selected_cases, turns):
            if (
                len(turn.input_images) != 1
                or turn.input_images[0].sha256 != case.observation.sha256
            ):
                diagnostic_failures.append(f"{case.id} input image binding is incomplete")
        final_turn = turns[-1]
        if not final_turn.resolved_model_id or (
            candidate.expected_model_family not in final_turn.resolved_model_id
        ):
            diagnostic_failures.append(
                f"resolved model mismatch: {final_turn.resolved_model_id or 'unreported'}"
            )
        if final_turn.actual_effort != "medium":
            diagnostic_failures.append(f"actual effort is {final_turn.actual_effort}")
        formal_disqualifiers = [
            "fixture semantic truth is candidate/not_frozen",
            "warm-memory exclusion is unproven",
            (
                f"ten images cover only {fixture.semantic_family_count} declared semantic "
                "families; image completeness is not semantic holdout completeness"
            ),
        ]
        decision_turns = turns[1:] if len(turns) > 1 else turns
        completed_at = utc_now()
        result = AFKJB3BenchmarkResultV1(
            id=identifier,
            benchmark_id=manifest.benchmark_id,
            candidate_id=candidate.id,
            repetition=repetition,
            facility_contract_sha256=contract.facility_contract_sha256,
            continuity_manifest_sha256=manifest_sha256,
            fixture_path=_display_path(fixture_path, self.repository_root),
            fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
            provider=candidate.provider,
            requested_model_selector=candidate.cli_model_selector,
            external_session_id=session.external_session_id,
            requested_case_count=requested_count,
            requested_case_ids=[item.id for item in selected_cases],
            completed_case_count=completed_count,
            full_image_diagnostic_set=full_image_set,
            formal_quality_eligible=False,
            formal_disqualifiers=formal_disqualifiers,
            diagnostic_quality_pass=not diagnostic_failures,
            diagnostic_failures=diagnostic_failures,
            expected_recall=expected_recall,
            important_recall=important_recall,
            precision=precision,
            evidence_complete_rate=(evidence_complete / proposal_count)
            if proposal_count
            else 0.0,
            safety_violation_count=safety_violations,
            unmatched_proposal_count=unmatched_count,
            unmatched_proposal_rate=(unmatched_count / proposal_count)
            if proposal_count
            else 0.0,
            same_native_session=same_session,
            decision_p50_seconds=_percentile(
                [item.duration_seconds for item in decision_turns], 0.50
            ),
            decision_p95_seconds=_percentile(
                [item.duration_seconds for item in decision_turns], 0.95
            ),
            cases=case_results,
            turns=turns,
            raw_runtime_root=_display_path(run_root, self.repository_root),
            status=(
                "succeeded"
                if completed_count == requested_count
                and all(item.status == "succeeded" for item in turns)
                else "failed"
            ),
            started_at=started_at,
            completed_at=completed_at,
        )
        result_path = run_root / "result.json"
        _publish_json_once(result_path, result)
        return result, result_path


def compare_b0_results(results: list[AFKJB0BenchmarkResultV1]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for candidate_id in sorted({item.candidate_id for item in results}):
        samples = [item for item in results if item.candidate_id == candidate_id]
        cold = [item.turns[0].duration_seconds for item in samples if item.turns]
        warm = [item.turns[1].duration_seconds for item in samples if len(item.turns) > 1]
        rows.append(
            {
                "candidate_id": candidate_id,
                "samples": len(samples),
                "quality_passes": sum(item.quality_pass for item in samples),
                "b0_accuracy": sum(item.b0_correct for item in samples)
                / (10 * len(samples)),
                "warm_accuracy": sum(item.warm_probe_correct for item in samples)
                / (4 * len(samples)),
                "cold_duration_median_seconds": _median(cold),
                "warm_duration_median_seconds": _median(warm),
                "unexpected_tool_events": sum(
                    turn.unexpected_tool_events
                    for item in samples
                    for turn in item.turns
                ),
                "input_tokens": sum(
                    turn.usage.input_tokens for item in samples for turn in item.turns
                ),
                "cached_input_tokens": sum(
                    turn.usage.cached_input_tokens for item in samples for turn in item.turns
                ),
                "cache_creation_input_tokens": sum(
                    turn.usage.cache_creation_input_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "output_tokens": sum(
                    turn.usage.output_tokens for item in samples for turn in item.turns
                ),
            }
        )
    return {
        "schema": "game-observatory.ai-player.afkj-external-agent-b0-comparison.v1",
        "benchmark_id": "afkj_external_agent_continuity_v1",
        "task_id": "B0",
        "rows": rows,
        "selection_allowed": bool(rows)
        and all(
            row["samples"] >= 3 and row["quality_passes"] == row["samples"]
            for row in rows
        ),
        "selection_rule": (
            "each candidate needs at least three samples and every included sample must "
            "pass quality before speed can select a role"
        ),
    }


def load_b0_result(path: Path) -> AFKJB0BenchmarkResultV1:
    return AFKJB0BenchmarkResultV1.model_validate_json(path.read_text(encoding="utf-8"))


def compare_b1_results(results: list[AFKJB1BenchmarkResultV1]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for candidate_id in sorted({item.candidate_id for item in results}):
        samples = [item for item in results if item.candidate_id == candidate_id]
        complete = [item for item in samples if item.complete_case_set]
        formal = [item for item in samples if item.formal_quality_eligible]
        turns = [turn for item in complete for turn in item.turns[1:]]
        diagnostic_turns = [turn for item in samples for turn in item.turns[1:]]
        total_cases = sum(item.requested_case_count for item in complete)
        diagnostic_cases = sum(item.requested_case_count for item in samples)
        rows.append(
            {
                "candidate_id": candidate_id,
                "samples": len(samples),
                "complete_samples": len(complete),
                "formal_samples": len(formal),
                "scoring_profiles": sorted(
                    {item.scoring_profile for item in samples}
                ),
                "quality_passes": sum(item.quality_pass for item in formal),
                "known_state_accuracy": (
                    sum(item.state_correct for item in complete) / total_cases
                    if total_cases
                    else None
                ),
                "known_target_accuracy": (
                    sum(item.target_hits for item in complete) / total_cases
                    if total_cases
                    else None
                ),
                "known_object_accuracy": (
                    sum(item.object_correct for item in complete) / total_cases
                    if total_cases
                    else None
                ),
                "raw_visual_target_accuracy": (
                    sum(item.raw_target_hits for item in complete)
                    / sum(item.raw_target_estimate_count for item in complete)
                    if sum(item.raw_target_estimate_count for item in complete)
                    else None
                ),
                "underlay_mispoint_count": sum(
                    item.underlay_mispoint_count for item in complete
                ),
                "diagnostic_case_count": diagnostic_cases,
                "diagnostic_same_session_samples": sum(
                    item.same_native_session for item in samples
                ),
                "diagnostic_state_accuracy": (
                    sum(item.state_correct for item in samples) / diagnostic_cases
                    if diagnostic_cases
                    else None
                ),
                "diagnostic_target_accuracy": (
                    sum(item.target_hits for item in samples) / diagnostic_cases
                    if diagnostic_cases
                    else None
                ),
                "diagnostic_object_accuracy": (
                    sum(item.object_correct for item in samples) / diagnostic_cases
                    if diagnostic_cases
                    else None
                ),
                "diagnostic_raw_visual_target_accuracy": (
                    sum(item.raw_target_hits for item in samples)
                    / sum(item.raw_target_estimate_count for item in samples)
                    if sum(item.raw_target_estimate_count for item in samples)
                    else None
                ),
                "diagnostic_warm_decision_p50_seconds": _percentile(
                    [turn.duration_seconds for turn in diagnostic_turns],
                    0.50,
                ),
                "diagnostic_warm_decision_p95_seconds": _percentile(
                    [turn.duration_seconds for turn in diagnostic_turns],
                    0.95,
                ),
                "diagnostic_input_tokens": sum(
                    turn.usage.input_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "diagnostic_cached_input_tokens": sum(
                    turn.usage.cached_input_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "diagnostic_cache_creation_input_tokens": sum(
                    turn.usage.cache_creation_input_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "diagnostic_output_tokens": sum(
                    turn.usage.output_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "warm_decision_p50_seconds": _percentile(
                    [turn.duration_seconds for turn in turns],
                    0.50,
                ),
                "warm_decision_p95_seconds": _percentile(
                    [turn.duration_seconds for turn in turns],
                    0.95,
                ),
                "input_tokens": sum(
                    turn.usage.input_tokens for item in complete for turn in item.turns
                ),
                "cached_input_tokens": sum(
                    turn.usage.cached_input_tokens
                    for item in complete
                    for turn in item.turns
                ),
                "output_tokens": sum(
                    turn.usage.output_tokens for item in complete for turn in item.turns
                ),
            }
        )
    selection_allowed = bool(rows) and all(
        row["formal_samples"] >= 3
        and row["quality_passes"] == row["formal_samples"]
        and row["scoring_profiles"] == ["layered-locator-v2"]
        for row in rows
    )
    requested_case_sets = {tuple(item.requested_case_ids) for item in results}
    scoring_profiles = {item.scoring_profile for item in results}
    diagnostic_comparable = bool(results) and len(requested_case_sets) == 1 and (
        scoring_profiles == {"layered-locator-v2"}
    )
    return {
        "schema": "game-observatory.ai-player.afkj-external-agent-b1-comparison.v1",
        "benchmark_id": "afkj_external_agent_continuity_v1",
        "task_id": "B1",
        "rows": rows,
        "diagnostic_comparable": diagnostic_comparable,
        "diagnostic_case_ids": (
            list(next(iter(requested_case_sets))) if diagnostic_comparable else []
        ),
        "diagnostic_rule": (
            "matched non-frozen smoke cases may compare runtime diagnostics only; "
            "they cannot select a production role"
        ),
        "selection_allowed": selection_allowed,
        "selection_rule": (
            "each candidate needs at least three formal 20-case samples and every "
            "included formal sample must pass all quality gates"
        ),
    }


def load_b1_result(path: Path) -> AFKJB1BenchmarkResultV1:
    return AFKJB1BenchmarkResultV1.model_validate_json(path.read_text(encoding="utf-8"))


def compare_b3_results(results: list[AFKJB3BenchmarkResultV1]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for candidate_id in sorted({item.candidate_id for item in results}):
        samples = [item for item in results if item.candidate_id == candidate_id]
        total_cases = sum(item.requested_case_count for item in samples)
        total_proposals = sum(
            case.score.proposal_count for item in samples for case in item.cases
        )
        eligible = sum(
            case.score.eligible_proposal_count for item in samples for case in item.cases
        )
        matched = sum(
            case.matched_proposal_count for item in samples for case in item.cases
        )
        evidence_complete = sum(
            case.score.evidence_complete_rate * case.score.proposal_count
            for item in samples
            for case in item.cases
        )
        turns = [turn for item in samples for turn in item.turns[1:]]
        rows.append(
            {
                "candidate_id": candidate_id,
                "samples": len(samples),
                "diagnostic_quality_passes": sum(
                    item.diagnostic_quality_pass for item in samples
                ),
                "full_image_diagnostic_samples": sum(
                    item.full_image_diagnostic_set for item in samples
                ),
                "formal_samples": 0,
                "case_count": total_cases,
                "expected_recall": _median(
                    [item.expected_recall for item in samples]
                ),
                "important_recall": _median(
                    [item.important_recall for item in samples]
                ),
                "precision": (matched / eligible) if eligible else 0.0,
                "evidence_complete_rate": (
                    evidence_complete / total_proposals if total_proposals else 0.0
                ),
                "safety_violation_count": sum(
                    item.safety_violation_count for item in samples
                ),
                "unmatched_proposal_rate": (
                    sum(item.unmatched_proposal_count for item in samples)
                    / total_proposals
                    if total_proposals
                    else 0.0
                ),
                "warm_decision_p50_seconds": _percentile(
                    [turn.duration_seconds for turn in turns], 0.50
                ),
                "warm_decision_p95_seconds": _percentile(
                    [turn.duration_seconds for turn in turns], 0.95
                ),
                "input_tokens": sum(
                    turn.usage.input_tokens for item in samples for turn in item.turns
                ),
                "cached_input_tokens": sum(
                    turn.usage.cached_input_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "cache_creation_input_tokens": sum(
                    turn.usage.cache_creation_input_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "output_tokens": sum(
                    turn.usage.output_tokens for item in samples for turn in item.turns
                ),
            }
        )
    requested_case_sets = {tuple(item.requested_case_ids) for item in results}
    fixture_contracts = {
        (
            item.facility_contract_sha256,
            item.continuity_manifest_sha256,
            item.fixture_sha256,
        )
        for item in results
    }
    diagnostic_comparable = bool(results) and len(requested_case_sets) == 1 and len(
        fixture_contracts
    ) == 1
    return {
        "schema": "game-observatory.ai-player.afkj-external-agent-b3-comparison.v1",
        "benchmark_id": "afkj_external_agent_continuity_v1",
        "task_id": "B3",
        "rows": rows,
        "diagnostic_comparable": diagnostic_comparable,
        "diagnostic_case_ids": (
            list(next(iter(requested_case_sets))) if diagnostic_comparable else []
        ),
        "unmatched_semantics": "offline_expected_probe_proxy_not_real_misclicks",
        "selection_allowed": False,
        "selection_rule": (
            "candidate/not_frozen images with unproven warm-memory exclusion cannot "
            "select a production role; ten images only establish image-diagnostic coverage"
        ),
    }


def load_b3_result(path: Path) -> AFKJB3BenchmarkResultV1:
    return AFKJB3BenchmarkResultV1.model_validate_json(path.read_text(encoding="utf-8"))


def _candidate(
    manifest: AFKJExternalAgentContinuityManifestV1,
    candidate_id: str,
) -> ExternalAgentBenchmarkCandidateV1:
    for candidate in manifest.candidates:
        if candidate.id == candidate_id:
            return candidate
    raise ValueError(f"unknown benchmark candidate: {candidate_id}")


def _invocation_message(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1 | None,
) -> dict[str, Any] | None:
    if invocation is None:
        return None
    path = ledger.root / invocation.last_message_path
    return parse_json_object(path.read_text(encoding="utf-8", errors="replace"))


def _turn(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> ExternalAgentBenchmarkTurnV1:
    return ExternalAgentBenchmarkTurnV1(
        invocation_id=invocation.id,
        operation=invocation.operation,
        status=invocation.status,
        timeout_reason=invocation.timeout_reason,
        external_session_id=invocation.external_session_id,
        resolved_model_id=invocation.resolved_model_id,
        model_evidence=invocation.model_evidence,
        actual_effort=invocation.actual_effort,
        effort_evidence=invocation.effort_evidence,
        duration_seconds=invocation.duration_seconds,
        provider_duration_seconds=invocation.provider_duration_seconds,
        time_to_first_token_seconds=invocation.time_to_first_token_seconds,
        provider_cost_usd=invocation.provider_cost_usd,
        usage=invocation.usage,
        input_images=invocation.input_images,
        unexpected_tool_events=_count_tool_events(ledger, invocation),
        warning_count=len(invocation.warnings),
        error=invocation.error,
        event_log_path=invocation.event_log_path,
        event_log_sha256=invocation.event_log_sha256,
        last_message_path=invocation.last_message_path,
        last_message_sha256=invocation.last_message_sha256,
    )


def _count_tool_events(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> int:
    path = ledger.root / invocation.event_log_path
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = record.get("event") if isinstance(record, dict) else None
        if not isinstance(event, dict):
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        if item.get("type") in {"command_execution", "mcp_tool_call", "tool_call"}:
            count += 1
        count += sum(
            1
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        )
    return count


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _display_path(path: Path, repository_root: Path) -> str:
    try:
        return str(path.relative_to(repository_root))
    except ValueError:
        return str(path)


def run_b0_sync(**kwargs: Any) -> tuple[AFKJB0BenchmarkResultV1, Path]:
    runner = kwargs.pop("runner")
    return asyncio.run(runner.run_b0(**kwargs))


def run_b1_sync(**kwargs: Any) -> tuple[AFKJB1BenchmarkResultV1, Path]:
    runner = kwargs.pop("runner")
    return asyncio.run(runner.run_b1(**kwargs))


def run_b3_sync(**kwargs: Any) -> tuple[AFKJB3BenchmarkResultV1, Path]:
    runner = kwargs.pop("runner")
    return asyncio.run(runner.run_b3(**kwargs))


__all__ = [
    "AFKJB0BenchmarkResultV1",
    "AFKJB0QuestionV1",
    "AFKJB1BenchmarkResultV1",
    "AFKJB1CaseResultV1",
    "AFKJB1CaseV1",
    "AFKJB1FixtureV1",
    "AFKJB3BenchmarkResultV1",
    "AFKJB3CaseResultV1",
    "AFKJB3CaseV1",
    "AFKJB3FixtureV1",
    "AFKJExternalAgentBenchmarkRunner",
    "B0_QUESTIONS",
    "B1_CASE_SPECS",
    "B1_STATE_CATALOG",
    "B1_STATE_FAMILIES",
    "B1_STATE_LABELS",
    "B3_SOURCE_SPECS",
    "build_b0_prompt",
    "build_b0_warm_prompt",
    "build_b1_fixture",
    "build_b1_prompt",
    "build_b3_fixture",
    "build_b3_prompt",
    "compare_b0_results",
    "compare_b1_results",
    "compare_b3_results",
    "export_b3_fixture",
    "load_b0_result",
    "load_b1_result",
    "load_b3_result",
    "parse_json_object",
    "run_b0_sync",
    "run_b1_sync",
    "run_b3_sync",
    "score_b0_answers",
    "score_b1_case",
    "score_b3_case",
    "score_warm_probe",
]
