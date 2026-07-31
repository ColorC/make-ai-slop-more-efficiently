from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


LIFECYCLE_ACTION_TYPES = frozenset({"launch", "force_stop"})


class ContentKind(str, Enum):
    direct_observation = "direct_observation"
    official_statement = "official_statement"
    player_voice = "player_voice"
    analyst_interpretation = "analyst_interpretation"


FeedbackSourceType = Literal[
    "player_comment",
    "player_discussion",
    "media_score",
    "media_article",
    "media_review",
    "objective_data",
    "estimated_data",
]


class SourceRating(BaseModel):
    value: float
    scale_min: float = 0
    scale_max: float
    label: str | None = None
    rating_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scale(self) -> SourceRating:
        if self.scale_max <= self.scale_min:
            raise ValueError("rating scale_max must be greater than scale_min")
        if not self.scale_min <= self.value <= self.scale_max:
            raise ValueError("rating value must be within its scale")
        return self


class EstimationMethod(BaseModel):
    method: str = Field(min_length=1)
    basis: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    range_low: float | None = None
    range_high: float | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> EstimationMethod:
        if (self.range_low is None) != (self.range_high is None):
            raise ValueError("estimated range requires both range_low and range_high")
        if (
            self.range_low is not None
            and self.range_high is not None
            and self.range_low > self.range_high
        ):
            raise ValueError("estimated range_low must not exceed range_high")
        return self


DESIGN_SPEC_CONTRACT_V03 = "reverse-engineered-game-design-spec.v0.3"

DesignSectionName = Literal[
    "scope",
    "system_overview",
    "player_goals",
    "entry_unlock",
    "core_loop",
    "information_architecture",
    "surface_design",
    "interaction_flow",
    "state_matrix",
    "rules_mechanics",
    "resources_economy",
    "progression_balance",
    "feedback",
    "tutorial",
    "failure_recovery",
    "dependencies",
    "player_voice",
    "version_provenance",
]

DESIGN_SECTIONS: tuple[str, ...] = (
    "scope",
    "system_overview",
    "player_goals",
    "entry_unlock",
    "core_loop",
    "information_architecture",
    "surface_design",
    "interaction_flow",
    "state_matrix",
    "rules_mechanics",
    "resources_economy",
    "progression_balance",
    "feedback",
    "tutorial",
    "failure_recovery",
    "dependencies",
    "player_voice",
    "version_provenance",
)

ALWAYS_REQUIRED_DESIGN_SECTIONS: frozenset[str] = frozenset(
    {
        "scope",
        "system_overview",
        "player_goals",
        "entry_unlock",
        "core_loop",
        "information_architecture",
        "surface_design",
        "interaction_flow",
        "state_matrix",
        "rules_mechanics",
        "feedback",
        "failure_recovery",
        "dependencies",
        "player_voice",
        "version_provenance",
    }
)


class SourceRef(BaseModel):
    id: str
    kind: ContentKind
    title: str
    url: str
    locator: str | None = None
    author: str | None = None
    published_at: str | None = None
    captured_at: str = Field(default_factory=utc_now)
    resolved_url: str | None = None
    content_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    content_type: str | None = None
    content_bytes: int | None = Field(default=None, ge=0)
    platform: str | None = None
    source_type: FeedbackSourceType | None = None
    account: str | None = None
    locale: str | None = None
    engagement: dict[str, int | float] = Field(default_factory=dict)
    rating: SourceRating | None = None
    data_scope: dict[str, Any] = Field(default_factory=dict)
    estimation_method: EstimationMethod | None = None
    version_context: str | None = None
    public: bool = True
    note: str | None = None
    usage_policy: Literal["link_only", "short_excerpt", "internal_evidence"] = "link_only"
    license_note: str | None = None
    status: Literal["active", "retracted"] = "active"
    retracted_at: str | None = None
    retraction_reason: str | None = None

    @model_validator(mode="after")
    def validate_feedback_source(self) -> SourceRef:
        if self.source_type is None:
            return self
        if not self.platform or not self.platform.strip():
            raise ValueError("feedback source requires a real platform name")
        if not self.url.startswith(("https://", "http://")):
            raise ValueError("feedback source requires an original public http(s) URL")
        if not self.public:
            raise ValueError("feedback source must keep its original platform link public")
        parsed = urlparse(self.url)
        if parsed.hostname in {"localhost", "localhost.localdomain"}:
            raise ValueError("feedback source URL must not target a local host")
        if parsed.hostname:
            try:
                host_ip = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                host_ip = None
            if host_ip and (
                host_ip.is_private
                or host_ip.is_loopback
                or host_ip.is_link_local
                or host_ip.is_reserved
            ):
                raise ValueError("feedback source URL must target a public host")
        if self.source_type == "player_comment":
            if not (self.author or self.account):
                raise ValueError("player comment requires author or account")
            if not self.locator:
                raise ValueError("player comment requires a precise locator")
        if self.source_type == "media_score" and self.rating is None:
            raise ValueError("media score requires rating value and scale")
        if self.source_type == "objective_data" and not self.data_scope:
            raise ValueError("objective data requires data_scope")
        if self.source_type == "estimated_data" and self.estimation_method is None:
            raise ValueError("estimated data requires estimation_method")
        return self


class SourceSnapshot(BaseModel):
    id: str
    source_id: str
    content_sha256: str
    locator: str | None = None
    excerpt: str | None = None
    captured_at: str = Field(default_factory=utc_now)
    status: Literal["active", "retracted"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRef(BaseModel):
    id: str
    kind: Literal[
        "screenshot",
        "video",
        "video_frame",
        "trace",
        "ui_tree",
        "source",
        "runtime_state",
        "annotated_plate",
        "layout_spec",
        "wireframe",
        "wireflow",
        "state_diagram",
        "interaction_diagram",
        "resource_diagram",
        "balance_table",
        "feedback_timeline",
    ]
    path: str
    sha256: str
    # Historical rows predate capture-time provenance. New evidence recorders
    # populate this field; day-sensitive facilities reject a missing value
    # instead of guessing from a mutable file mtime.
    captured_at: str | None = None
    run_id: str | None = None
    locator: str | None = None
    media_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRef(BaseModel):
    id: str
    target_id: str
    adapter: str
    started_at: str
    ended_at: str | None = None
    status: Literal["running", "passed", "failed", "stopped"]
    build_scope_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class NormalizedRect(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)


class UIElementInstance(BaseModel):
    id: str
    role: str
    label: str | None = None
    text: str | None = None
    bounds: NormalizedRect | None = None
    parent_id: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class Surface(BaseModel):
    id: str
    title: str
    kind: Literal["page", "modal", "overlay", "combat", "world", "system"]
    description: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    elements: list[UIElementInstance] = Field(default_factory=list)
    publication_required: bool = True


class Claim(BaseModel):
    id: str
    kind: ContentKind
    statement: str
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    flow_node_id: str | None = None
    review_status: Literal["pending", "reviewed", "retracted"] = "pending"


class BuildScope(BaseModel):
    id: str
    game_id: str
    platform: str
    version: str = "unknown"
    region: str = "unknown"
    locale: str = "zh-CN"
    account_stage: str = "unknown"
    device: str = "unknown"
    package_name: str = "unknown"
    server: str = "unknown"
    resolution: str = "unknown"
    captured_at: str = Field(default_factory=utc_now)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class Game(BaseModel):
    id: str
    title: str
    slug: str | None = None
    localized_title: str | None = None
    aliases: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class Play(BaseModel):
    id: str
    slug: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class SystemConcept(BaseModel):
    id: str
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    parent_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class SystemInstance(BaseModel):
    id: str
    concept_id: str
    build_scope_id: str
    title: str
    surface_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)


class FlowNode(BaseModel):
    id: str
    title: str
    description: str
    action: str | None = None
    state_before: str | None = None
    state_after: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    surface_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    next: list[str] = Field(default_factory=list)


class MechanismRuleSpec(BaseModel):
    title: str
    statement: str
    formula: str | None = None


class MechanismReaderCopy(BaseModel):
    concept_paragraphs: list[str] = Field(min_length=1)
    player_flow_paragraphs: list[str] = Field(min_length=1)
    rule_intro: str
    state_and_limits_paragraphs: list[str] = Field(min_length=1)
    system_link_paragraphs: list[str] = Field(min_length=1)
    image_caption: str


class MechanismSpec(BaseModel):
    id: str
    title: str
    description: str
    representation: Literal["rule", "formula", "state_machine", "pseudocode"]
    code: str | None = None
    definition: str | None = None
    player_result: str | None = None
    reader_copy: MechanismReaderCopy | None = None
    objects: list[str] = Field(default_factory=list)
    state_variables: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    rules: list[MechanismRuleSpec] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class ResourceDefinition(BaseModel):
    id: str
    title: str
    kind: Literal["currency", "material", "item", "energy", "facility", "progress", "other"]
    unit: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class ResourceRelation(BaseModel):
    id: str = ""
    resource: str
    role: Literal["source", "cost", "gate", "reward", "conversion"]
    description: str
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    from_resource_id: str | None = None
    to_resource_id: str | None = None


class ResourceModel(BaseModel):
    id: str
    title: str
    resources: list[ResourceDefinition] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class Observation(BaseModel):
    id: str
    statement: str
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class PlayerVoice(BaseModel):
    id: str
    summary: str
    theme: str
    sentiment: Literal["positive", "negative", "mixed", "question"]
    source_id: str
    system_node_id: str | None = None
    target_object_ids: list[str] = Field(default_factory=list)
    version_context: str = "unknown"
    quote: str | None = None
    quote_locator: str | None = None
    context: str | None = None
    language: str = "unknown"
    tags: list[str] = Field(default_factory=list)
    review_status: Literal["pending", "reviewed", "rejected"] = "reviewed"
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    review_note: str | None = None
    status: Literal["active", "retracted"] = "active"
    retracted_at: str | None = None
    retraction_reason: str | None = None


class CommunityFeedbackItem(BaseModel):
    id: str
    source_type: FeedbackSourceType
    content_scope: Literal["game", "play"] = "play"
    title: str
    summary: str
    source: SourceRef
    tags: list[str] = Field(default_factory=list)
    target_object_ids: list[str] = Field(default_factory=list)
    preview_artifact_id: str | None = None

    @model_validator(mode="after")
    def source_type_matches(self) -> CommunityFeedbackItem:
        if self.source.source_type != self.source_type:
            raise ValueError("community feedback source_type must match source.source_type")
        return self


class PlayScreenTag(BaseModel):
    surface_id: str
    tags: list[str] = Field(min_length=1)


class PlayRecord(BaseModel):
    id: str
    source_type: Literal["ai_player_live_run", "human_screen_recording"]
    title: str
    platform: str
    captured_at: str
    operator: str | None = None
    run_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def validate_exact_source(self) -> PlayRecord:
        if self.source_type == "ai_player_live_run" and not self.run_id:
            raise ValueError("AI player live run requires run_id")
        if self.source_type == "human_screen_recording" and not (
            self.source_ids or self.artifact_ids
        ):
            raise ValueError("human screen recording requires source_ids or artifact_ids")
        return self


class DemoReproduction(BaseModel):
    id: str
    title: str
    description: str
    url: str
    status: Literal["draft", "reviewed"] = "draft"
    covered_surface_ids: list[str] = Field(default_factory=list)
    covered_interaction_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def validate_demo_url(cls, value: str) -> str:
        if not value.startswith(("/", "https://", "http://")):
            raise ValueError("demo URL must be a local route or public http(s) URL")
        return value


class VoiceRecord(BaseModel):
    id: str
    report_id: str
    fingerprint: str
    voice: PlayerVoice
    created_at: str = Field(default_factory=utc_now)
    status: Literal["active", "retracted"] = "active"


class ObjectiveCheck(BaseModel):
    id: str
    description: str
    expected: Any
    actual: Any | None = None
    passed: bool | None = None


class BenchmarkTask(BaseModel):
    id: str
    title: str
    start_state: str
    goal: str
    allowed_actions: list[str]
    reset_method: str
    checks: list[ObjectiveCheck]
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    seq: int
    run_id: str
    event_type: str
    timestamp: str = Field(default_factory=utc_now)
    observation_artifact_ids: list[str] = Field(default_factory=list)
    action: dict[str, Any] | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    recovery: dict[str, Any] | None = None


class DesignStatement(BaseModel):
    id: str
    title: str
    statement: str
    kind: ContentKind
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class CoreLoopStep(BaseModel):
    id: str
    title: str
    player_action: str
    system_response: str
    state_before: str
    state_after: str
    cadence: str | None = None
    flow_node_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class CoreLoopSpec(BaseModel):
    id: str
    title: str
    player_goal: str
    entry_conditions: list[str]
    exit_conditions: list[str]
    steps: list[CoreLoopStep] = Field(min_length=2)
    cadence: str | None = None


class NavigationEdge(BaseModel):
    id: str
    from_surface_id: str
    to_surface_id: str
    trigger: str
    condition: str | None = None
    flow_node_ids: list[str] = Field(default_factory=list)


class InformationArchitectureSpec(BaseModel):
    id: str
    root_surface_ids: list[str] = Field(min_length=1)
    surface_ids: list[str] = Field(min_length=1)
    edges: list[NavigationEdge] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LayoutElementSpec(BaseModel):
    id: str
    ui_element_id: str
    bounds: NormalizedRect
    anchors: list[str] = Field(default_factory=list)
    alignment: list[str] = Field(default_factory=list)
    spacing: dict[str, float] = Field(default_factory=dict)
    z_index: int = 0
    scroll_behavior: str | None = None
    responsive_behavior: str | None = None


class LayoutSpec(BaseModel):
    id: str
    surface_id: str
    coordinate_space: Literal["normalized"] = "normalized"
    canvas_aspect_ratio: str
    safe_area: NormalizedRect | None = None
    elements: list[LayoutElementSpec] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class DesignArtifactSpec(BaseModel):
    id: str
    title: str
    kind: Literal[
        "annotated_plate",
        "layout_spec",
        "wireframe",
        "wireflow",
        "state_diagram",
        "interaction_diagram",
        "resource_diagram",
        "balance_table",
        "feedback_timeline",
    ]
    artifact_id: str
    surface_ids: list[str] = Field(default_factory=list)
    flow_node_ids: list[str] = Field(default_factory=list)
    derived_from_artifact_ids: list[str] = Field(min_length=1)
    generation_method: Literal["manual_reconstruction", "machine_reconstruction", "hybrid"]
    source_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    review_status: Literal["pending", "reviewed", "rejected"] = "pending"


class InteractionStep(BaseModel):
    id: str
    order: int = Field(ge=1)
    actor: Literal["player", "system"]
    action: str
    response: str | None = None
    state_before: str | None = None
    state_after: str | None = None
    surface_id: str | None = None
    ui_element_id: str | None = None
    flow_node_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class InteractionSpec(BaseModel):
    id: str
    title: str
    trigger: str
    preconditions: list[str]
    steps: list[InteractionStep] = Field(min_length=1)
    postconditions: list[str]
    branches: list[str] = Field(default_factory=list)
    failure_recovery_ids: list[str] = Field(default_factory=list)
    diagram_artifact_id: str
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class StateCase(BaseModel):
    id: str
    state: str
    condition: str
    visible: bool | None = None
    enabled: bool | None = None
    content: str | None = None
    feedback: list[str] = Field(default_factory=list)
    next_state: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class StateMatrix(BaseModel):
    id: str
    title: str
    subject_id: str
    dimensions: list[str] = Field(min_length=1)
    cases: list[StateCase] = Field(min_length=2)


class ProgressionAxis(BaseModel):
    id: str
    name: str
    unit: str
    stages: list[str] = Field(min_length=1)
    gates: list[str] = Field(default_factory=list)
    resets: list[str] = Field(default_factory=list)


class ProgressionSpec(BaseModel):
    id: str
    title: str
    axes: list[ProgressionAxis] = Field(min_length=1)
    pacing: list[str] = Field(default_factory=list)
    cross_system_effects: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class BalanceParameter(BaseModel):
    id: str
    name: str
    value_or_range: str
    unit: str | None = None
    tuning_role: str
    constraints: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class BalanceSpec(BaseModel):
    id: str
    title: str
    target_experience: str
    parameters: list[BalanceParameter] = Field(min_length=1)
    mechanism_ids: list[str] = Field(default_factory=list)
    table_artifact_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FeedbackSpec(BaseModel):
    id: str
    title: str
    trigger: str
    channels: list[Literal["visual", "animation", "audio", "haptic", "text", "numeric"]] = Field(min_length=1)
    timing: str
    success_behavior: str
    failure_behavior: str
    surface_ids: list[str] = Field(default_factory=list)
    ui_element_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class TutorialStep(BaseModel):
    id: str
    trigger: str
    instruction: str
    allowed_actions: list[str]
    blocked_actions: list[str] = Field(default_factory=list)
    completion_condition: str
    recovery: str
    flow_node_ids: list[str] = Field(default_factory=list)


class TutorialSpec(BaseModel):
    id: str
    title: str
    steps: list[TutorialStep] = Field(min_length=1)
    skippable: bool | None = None
    repeat_behavior: str
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class FailureRecoverySpec(BaseModel):
    id: str
    title: str
    failure_condition: str
    visible_behavior: str
    retained_state: str
    recovery_action: str
    irreversible_effects: list[str] = Field(default_factory=list)
    flow_node_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class DependencySpec(BaseModel):
    id: str
    title: str
    direction: Literal["upstream", "downstream", "shared"]
    target_system_id: str
    dependency: str
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class DesignSectionCoverage(BaseModel):
    section: DesignSectionName
    status: Literal["complete", "not_applicable", "incomplete"]
    object_ids: list[str] = Field(default_factory=list)
    rationale: str


class ReverseEngineeredGameDesignSpec(BaseModel):
    id: str
    contract_version: Literal["reverse-engineered-game-design-spec.v0.3"] = DESIGN_SPEC_CONTRACT_V03
    title: str
    scope_id: str
    system_instance_id: str
    overview: list[DesignStatement] = Field(min_length=1)
    player_goals: list[DesignStatement] = Field(min_length=1)
    entry_and_unlock: list[DesignStatement] = Field(min_length=1)
    core_loop: CoreLoopSpec
    information_architecture: InformationArchitectureSpec
    design_artifacts: list[DesignArtifactSpec] = Field(min_length=1)
    layout_specs: list[LayoutSpec] = Field(min_length=1)
    interaction_specs: list[InteractionSpec] = Field(min_length=1)
    state_matrices: list[StateMatrix] = Field(min_length=1)
    progression_specs: list[ProgressionSpec] = Field(default_factory=list)
    balance_specs: list[BalanceSpec] = Field(default_factory=list)
    feedback_specs: list[FeedbackSpec] = Field(min_length=1)
    tutorial_specs: list[TutorialSpec] = Field(default_factory=list)
    failure_recovery_specs: list[FailureRecoverySpec] = Field(min_length=1)
    dependency_specs: list[DependencySpec] = Field(min_length=1)
    monetization_specs: list[DesignStatement] = Field(default_factory=list)
    version_notes: list[DesignStatement] = Field(default_factory=list)
    mechanism_ids: list[str] = Field(min_length=1)
    resource_model_id: str | None = None
    resource_relation_ids: list[str] = Field(default_factory=list)
    player_voice_ids: list[str] = Field(min_length=1)
    section_coverage: list[DesignSectionCoverage] = Field(min_length=len(DESIGN_SECTIONS))
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class GameReport(BaseModel):
    id: str
    slug: str
    game_id: str
    game_title: str
    system_id: str
    system_title: str
    summary: str
    contract_version: Literal[
        "legacy-report-v0.2", "reverse-engineered-game-design-spec.v0.3"
    ] = "legacy-report-v0.2"
    migration_status: Literal[
        "legacy_draft",
        "needs_evidence",
        "needs_design_artifacts",
        "needs_sections",
        "review_ready",
        "publishable",
    ] = "legacy_draft"
    design_spec: ReverseEngineeredGameDesignSpec | None = None
    summary_claim: Claim | None = None
    scope: BuildScope
    game: Game | None = None
    play: Play | None = None
    system_concept: SystemConcept | None = None
    system_instance: SystemInstance | None = None
    resource_model: ResourceModel | None = None
    tags: list[str]
    status: Literal["draft", "review", "published"] = "draft"
    cover_artifact_id: str | None = None
    sources: list[SourceRef]
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    runs: list[RunRef] = Field(default_factory=list)
    surfaces: list[Surface] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    flow: list[FlowNode]
    mechanisms: list[MechanismSpec]
    resources: list[ResourceRelation] = Field(default_factory=list)
    player_voices: list[PlayerVoice] = Field(default_factory=list)
    community_feedback: list[CommunityFeedbackItem] = Field(default_factory=list)
    screen_tags: list[PlayScreenTag] = Field(default_factory=list)
    play_records: list[PlayRecord] = Field(default_factory=list)
    demo_reproductions: list[DemoReproduction] = Field(default_factory=list)
    benchmark_task: BenchmarkTask | None = None
    observations: list[Observation | str] = Field(default_factory=list)
    interpretations: list[Claim | str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().lower() for item in value if item.strip()})

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, value: list[SourceRef]) -> list[SourceRef]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source ids must be unique")
        return value

    @model_validator(mode="after")
    def ensure_game_play_hierarchy(self) -> GameReport:
        if self.game is None:
            self.game = Game(id=self.game_id, title=self.game_title, slug=self.game_id)
        self.game.id = self.game_id
        self.game.title = self.game_title
        if self.game.slug is None:
            self.game.slug = self.game.id
        if self.play is None:
            self.play = Play(
                id=self.system_id,
                slug=self.slug,
                title=self.system_title,
                tags=self.tags,
                description=self.summary,
            )
        self.play.id = self.system_id
        self.play.slug = self.slug
        self.play.title = self.system_title
        if not self.play.tags:
            self.play.tags = list(self.tags)
        if not self.play.description:
            self.play.description = self.summary
        return self

    def assert_storable(self) -> None:
        self.ensure_game_play_hierarchy()
        issues = self.provenance_issues()
        if issues:
            raise ValueError("report provenance invalid: " + "; ".join(issues))

    def assert_publishable(self) -> None:
        self.assert_storable()
        issues = self.publication_issues()
        if issues:
            raise ValueError("design spec publication invalid: " + "; ".join(issues))

    def design_object_ids(self) -> set[str]:
        ids = {
            self.id,
            self.scope.id,
            *(item.id for item in self.sources),
            *(item.id for item in self.artifacts),
            *(item.id for item in self.runs),
            *(item.id for item in self.surfaces),
            *(item.id for item in self.flow),
            *(item.id for item in self.mechanisms),
            *(item.id for item in self.resources),
            *(item.id for item in self.player_voices),
            *(item.id for item in self.community_feedback),
            *(item.id for item in self.claims),
        }
        for value in (
            self.game,
            self.play,
            self.system_concept,
            self.system_instance,
            self.resource_model,
        ):
            if value:
                ids.add(value.id)
        ids.update(item.id for item in self.observations if not isinstance(item, str))
        ids.update(item.id for item in self.interpretations if not isinstance(item, str))
        for surface in self.surfaces:
            ids.update(item.id for item in surface.elements)
        spec = self.design_spec
        if not spec:
            return ids
        ids.add(spec.id)
        for values in (
            spec.overview,
            spec.player_goals,
            spec.entry_and_unlock,
            spec.design_artifacts,
            spec.layout_specs,
            spec.interaction_specs,
            spec.state_matrices,
            spec.progression_specs,
            spec.balance_specs,
            spec.feedback_specs,
            spec.tutorial_specs,
            spec.failure_recovery_specs,
            spec.dependency_specs,
            spec.monetization_specs,
            spec.version_notes,
        ):
            ids.update(item.id for item in values)
        ids.add(spec.core_loop.id)
        ids.update(item.id for item in spec.core_loop.steps)
        ids.add(spec.information_architecture.id)
        ids.update(item.id for item in spec.information_architecture.edges)
        for layout in spec.layout_specs:
            ids.update(item.id for item in layout.elements)
        for interaction in spec.interaction_specs:
            ids.update(item.id for item in interaction.steps)
        for matrix in spec.state_matrices:
            ids.update(item.id for item in matrix.cases)
        for progression in spec.progression_specs:
            ids.update(item.id for item in progression.axes)
        for balance in spec.balance_specs:
            ids.update(item.id for item in balance.parameters)
        for tutorial in spec.tutorial_specs:
            ids.update(item.id for item in tutorial.steps)
        return ids

    def publication_issues(self) -> list[str]:
        issues: list[str] = []
        if self.contract_version != DESIGN_SPEC_CONTRACT_V03:
            issues.append(f"contract_version: expected {DESIGN_SPEC_CONTRACT_V03}")
        if self.migration_status not in {"review_ready", "publishable"}:
            issues.append("migration_status: design spec is not review_ready")
        spec = self.design_spec
        if spec is None:
            issues.append("design_spec: missing ReverseEngineeredGameDesignSpec")
            return sorted(set(issues))
        if spec.contract_version != self.contract_version:
            issues.append("design_spec: contract_version disagrees with report")
        if spec.scope_id != self.scope.id:
            issues.append("design_spec: scope_id disagrees with BuildScope")
        if not self.system_instance or spec.system_instance_id != self.system_instance.id:
            issues.append("design_spec: system_instance_id disagrees with SystemInstance")
        if not self.system_concept:
            issues.append("design_spec: SystemConcept is missing")
        else:
            if spec.title.strip() != self.system_concept.title.strip():
                issues.append("design_spec: title must be the neutral SystemConcept title")
            if self.system_title.strip() != self.system_concept.title.strip():
                issues.append("system_title: editorial thesis is not allowed in design spec identity")

        source_ids = {item.id for item in self.sources}
        artifact_by_id = {item.id: item for item in self.artifacts}
        artifact_ids = set(artifact_by_id)
        run_ids = {item.id for item in self.runs}
        surface_ids = {item.id for item in self.surfaces}
        flow_ids = {item.id for item in self.flow}
        mechanism_ids = {item.id for item in self.mechanisms}
        relation_ids = {item.id for item in self.resources}
        voice_ids = {item.id for item in self.player_voices}
        element_ids = {item.id for surface in self.surfaces for item in surface.elements}
        object_ids = self.design_object_ids()

        def check_links(
            object_id: str,
            sources: list[str],
            artifacts: list[str],
            run_id: str | None = None,
        ) -> None:
            missing_sources = sorted(set(sources) - source_ids)
            missing_artifacts = sorted(set(artifacts) - artifact_ids)
            if missing_sources:
                issues.append(f"{object_id}: missing sources {missing_sources}")
            if missing_artifacts:
                issues.append(f"{object_id}: missing artifacts {missing_artifacts}")
            if run_id and run_id not in run_ids:
                issues.append(f"{object_id}: missing run {run_id}")

        if set(spec.mechanism_ids) - mechanism_ids:
            issues.append(
                f"design_spec: missing mechanisms {sorted(set(spec.mechanism_ids) - mechanism_ids)}"
            )
        if spec.resource_model_id and (
            not self.resource_model or spec.resource_model_id != self.resource_model.id
        ):
            issues.append("design_spec: resource_model_id does not resolve")
        if set(spec.resource_relation_ids) - relation_ids:
            issues.append(
                "design_spec: missing resource relations "
                f"{sorted(set(spec.resource_relation_ids) - relation_ids)}"
            )
        if set(spec.player_voice_ids) - voice_ids:
            issues.append(
                f"design_spec: missing player voices {sorted(set(spec.player_voice_ids) - voice_ids)}"
            )
        if set(spec.source_ids) - source_ids:
            issues.append(f"design_spec: missing sources {sorted(set(spec.source_ids) - source_ids)}")
        if set(spec.artifact_ids) - artifact_ids:
            issues.append(
                f"design_spec: missing artifacts {sorted(set(spec.artifact_ids) - artifact_ids)}"
            )
        if set(spec.run_ids) - run_ids:
            issues.append(f"design_spec: missing runs {sorted(set(spec.run_ids) - run_ids)}")

        coverage_sections = [item.section for item in spec.section_coverage]
        duplicates = sorted({name for name in coverage_sections if coverage_sections.count(name) > 1})
        if duplicates:
            issues.append(f"section_coverage: duplicate sections {duplicates}")
        missing_sections = sorted(set(DESIGN_SECTIONS) - set(coverage_sections))
        if missing_sections:
            issues.append(f"section_coverage: missing sections {missing_sections}")
        for coverage in spec.section_coverage:
            if coverage.section in ALWAYS_REQUIRED_DESIGN_SECTIONS and coverage.status != "complete":
                issues.append(f"section:{coverage.section}: must be complete")
            if coverage.status == "incomplete":
                issues.append(f"section:{coverage.section}: incomplete")
            if coverage.status == "complete" and not coverage.object_ids:
                issues.append(f"section:{coverage.section}: complete section has no objects")
            if coverage.status == "not_applicable" and len(coverage.rationale.strip()) < 12:
                issues.append(f"section:{coverage.section}: not_applicable needs a concrete rationale")
            missing_objects = sorted(set(coverage.object_ids) - object_ids)
            if missing_objects:
                issues.append(f"section:{coverage.section}: missing objects {missing_objects}")

        if set(spec.information_architecture.surface_ids) != surface_ids:
            issues.append("information_architecture: must enumerate every Surface")
        if set(spec.information_architecture.root_surface_ids) - surface_ids:
            issues.append("information_architecture: root Surface does not resolve")
        for edge in spec.information_architecture.edges:
            if edge.from_surface_id not in surface_ids or edge.to_surface_id not in surface_ids:
                issues.append(f"{edge.id}: navigation Surface does not resolve")
            if set(edge.flow_node_ids) - flow_ids:
                issues.append(f"{edge.id}: navigation flow node does not resolve")

        public_visual_ids = {
            item.id
            for item in self.artifacts
            if item.kind in {"screenshot", "video_frame"} and item.metadata.get("public") is True
        }
        reviewed_design_artifacts = [
            item for item in spec.design_artifacts if item.review_status == "reviewed"
        ]
        for surface in self.surfaces:
            if not surface.publication_required:
                continue
            if not (set(surface.artifact_ids) & public_visual_ids):
                issues.append(f"{surface.id}: missing public screenshot or video frame")
            page_derivatives = [
                item
                for item in reviewed_design_artifacts
                if surface.id in item.surface_ids
                and item.kind in {"annotated_plate", "layout_spec", "wireframe"}
            ]
            if not page_derivatives:
                issues.append(f"{surface.id}: missing reviewed annotated plate, layout, or wireframe")
            if not any(layout.surface_id == surface.id for layout in spec.layout_specs):
                issues.append(f"{surface.id}: missing machine-readable LayoutSpec")
        for node in self.flow:
            if not (set(node.artifact_ids) & public_visual_ids):
                issues.append(f"{node.id}: missing public screenshot or video frame")
            if set(node.surface_ids) - surface_ids:
                issues.append(f"{node.id}: missing linked Surface")

        design_artifact_ids = {item.id for item in spec.design_artifacts}
        for item in spec.design_artifacts:
            artifact = artifact_by_id.get(item.artifact_id)
            if artifact is None:
                issues.append(f"{item.id}: output artifact does not resolve")
            elif artifact.kind != item.kind:
                issues.append(f"{item.id}: output artifact kind disagrees with DesignArtifactSpec")
            elif artifact.metadata.get("public") is not True:
                issues.append(f"{item.id}: output artifact is not publishable")
            missing_inputs = sorted(set(item.derived_from_artifact_ids) - artifact_ids)
            if missing_inputs:
                issues.append(f"{item.id}: missing derivation inputs {missing_inputs}")
            if item.review_status != "reviewed":
                issues.append(f"{item.id}: derived design artifact is not reviewed")
            if set(item.surface_ids) - surface_ids:
                issues.append(f"{item.id}: missing Surface reference")
            if set(item.flow_node_ids) - flow_ids:
                issues.append(f"{item.id}: missing flow node reference")
            check_links(item.id, item.source_ids, item.derived_from_artifact_ids, item.run_id)
        if not any(
            item.kind in {"wireflow", "interaction_diagram"}
            and item.review_status == "reviewed"
            and item.flow_node_ids
            for item in spec.design_artifacts
        ):
            issues.append("interaction_flow: missing reviewed Wireflow or interaction diagram")

        for layout in spec.layout_specs:
            if layout.surface_id not in surface_ids:
                issues.append(f"{layout.id}: Surface does not resolve")
            for element in layout.elements:
                if element.ui_element_id not in element_ids:
                    issues.append(f"{element.id}: UI element does not resolve")
            check_links(layout.id, layout.source_ids, layout.artifact_ids, layout.run_id)
        for interaction in spec.interaction_specs:
            if interaction.diagram_artifact_id not in artifact_ids:
                issues.append(f"{interaction.id}: interaction diagram artifact does not resolve")
            if not any(
                item.artifact_id == interaction.diagram_artifact_id
                and item.id in design_artifact_ids
                and item.kind in {"wireflow", "interaction_diagram"}
                for item in spec.design_artifacts
            ):
                issues.append(f"{interaction.id}: diagram is not a Wireflow/interaction DesignArtifact")
            check_links(interaction.id, interaction.source_ids, interaction.artifact_ids, interaction.run_id)
            for step in interaction.steps:
                if step.surface_id and step.surface_id not in surface_ids:
                    issues.append(f"{step.id}: Surface does not resolve")
                if step.ui_element_id and step.ui_element_id not in element_ids:
                    issues.append(f"{step.id}: UI element does not resolve")
                if step.flow_node_id and step.flow_node_id not in flow_ids:
                    issues.append(f"{step.id}: flow node does not resolve")
                check_links(step.id, step.source_ids, step.artifact_ids)
        for matrix in spec.state_matrices:
            if matrix.subject_id not in object_ids:
                issues.append(f"{matrix.id}: state-matrix subject does not resolve")
            for case in matrix.cases:
                check_links(case.id, case.source_ids, case.artifact_ids)

        statements = [
            *spec.overview,
            *spec.player_goals,
            *spec.entry_and_unlock,
            *spec.monetization_specs,
            *spec.version_notes,
        ]
        for statement in statements:
            check_links(statement.id, statement.source_ids, statement.artifact_ids, statement.run_id)
            if not (statement.source_ids or statement.artifact_ids or statement.run_id):
                issues.append(f"{statement.id}: design statement has no evidence")
        for step in spec.core_loop.steps:
            if set(step.flow_node_ids) - flow_ids:
                issues.append(f"{step.id}: core-loop flow node does not resolve")
            check_links(step.id, step.source_ids, step.artifact_ids, step.run_id)
        for progression in spec.progression_specs:
            check_links(
                progression.id,
                progression.source_ids,
                progression.artifact_ids,
                progression.run_id,
            )
        for balance in spec.balance_specs:
            if set(balance.mechanism_ids) - mechanism_ids:
                issues.append(f"{balance.id}: balance mechanism does not resolve")
            if set(balance.table_artifact_ids) - artifact_ids:
                issues.append(f"{balance.id}: balance table artifact does not resolve")
            for parameter in balance.parameters:
                check_links(parameter.id, parameter.source_ids, [])
        for feedback in spec.feedback_specs:
            if set(feedback.surface_ids) - surface_ids:
                issues.append(f"{feedback.id}: feedback Surface does not resolve")
            if set(feedback.ui_element_ids) - element_ids:
                issues.append(f"{feedback.id}: feedback UI element does not resolve")
            check_links(feedback.id, feedback.source_ids, feedback.artifact_ids, feedback.run_id)
        for tutorial in spec.tutorial_specs:
            check_links(tutorial.id, tutorial.source_ids, tutorial.artifact_ids, tutorial.run_id)
            for step in tutorial.steps:
                if set(step.flow_node_ids) - flow_ids:
                    issues.append(f"{step.id}: tutorial flow node does not resolve")
        for failure in spec.failure_recovery_specs:
            if set(failure.flow_node_ids) - flow_ids:
                issues.append(f"{failure.id}: failure flow node does not resolve")
            check_links(failure.id, failure.source_ids, failure.artifact_ids, failure.run_id)
        for dependency in spec.dependency_specs:
            check_links(
                dependency.id,
                dependency.source_ids,
                dependency.artifact_ids,
                dependency.run_id,
            )
        for voice in self.player_voices:
            if voice.id not in spec.player_voice_ids:
                continue
            targets = set(voice.target_object_ids)
            if voice.system_node_id:
                targets.add(voice.system_node_id)
            if not targets:
                issues.append(f"{voice.id}: player voice is not bound to a design object")
            missing_targets = sorted(targets - object_ids)
            if missing_targets:
                issues.append(f"{voice.id}: missing target objects {missing_targets}")
        source_by_id = {item.id: item for item in self.sources}
        for item in self.community_feedback:
            canonical_source = source_by_id.get(item.source.id)
            if canonical_source is None:
                issues.append(f"{item.id}: community feedback source does not resolve")
            elif canonical_source.model_dump(mode="json") != item.source.model_dump(mode="json"):
                issues.append(f"{item.id}: embedded source disagrees with canonical source")
            missing_targets = sorted(set(item.target_object_ids) - object_ids)
            if missing_targets:
                issues.append(f"{item.id}: missing target objects {missing_targets}")

        tagged_surface_ids: set[str] = set()
        for tag_binding in self.screen_tags:
            if tag_binding.surface_id in tagged_surface_ids:
                issues.append(f"{tag_binding.surface_id}: duplicate screen tag binding")
            tagged_surface_ids.add(tag_binding.surface_id)
            if tag_binding.surface_id not in surface_ids:
                issues.append(f"{tag_binding.surface_id}: screen tag Surface does not resolve")
        for record in self.play_records:
            check_links(record.id, record.source_ids, record.artifact_ids, record.run_id)
        interaction_ids = {item.id for item in spec.interaction_specs}
        for demo in self.demo_reproductions:
            if demo.status != "reviewed":
                issues.append(f"{demo.id}: Demo reproduction is not reviewed")
            missing_surfaces = sorted(set(demo.covered_surface_ids) - surface_ids)
            if missing_surfaces:
                issues.append(f"{demo.id}: missing covered Surfaces {missing_surfaces}")
            missing_interactions = sorted(set(demo.covered_interaction_ids) - interaction_ids)
            if missing_interactions:
                issues.append(f"{demo.id}: missing covered interactions {missing_interactions}")
            check_links(demo.id, demo.source_ids, demo.artifact_ids)

        conditional_status = {item.section: item.status for item in spec.section_coverage}
        if conditional_status.get("resources_economy") == "complete" and (
            not spec.resource_model_id or not spec.resource_relation_ids
        ):
            issues.append("section:resources_economy: complete but resource objects are missing")
        if conditional_status.get("progression_balance") == "complete" and (
            not spec.progression_specs or not spec.balance_specs
        ):
            issues.append("section:progression_balance: complete but progression/balance objects are missing")
        if conditional_status.get("tutorial") == "complete" and not spec.tutorial_specs:
            issues.append("section:tutorial: complete but TutorialSpec is missing")

        return sorted(set(issues))

    def provenance_issues(self) -> list[str]:
        """Return deterministic, object-level provenance and reference failures."""
        issues: list[str] = []
        source_ids = {item.id for item in self.sources}
        artifact_ids = {item.id for item in self.artifacts}
        run_ids = {item.id for item in self.runs}
        flow_ids = {item.id for item in self.flow}
        surface_ids = {item.id for item in self.surfaces}
        relation_ids = {item.id for item in self.resources}

        def check_refs(
            object_id: str,
            sources: list[str],
            artifacts: list[str],
            run_id: str | None,
            *,
            evidence_required: bool = True,
        ) -> None:
            missing_sources = sorted(set(sources) - source_ids)
            missing_artifacts = sorted(set(artifacts) - artifact_ids)
            if missing_sources:
                issues.append(f"{object_id}: missing sources {missing_sources}")
            if missing_artifacts:
                issues.append(f"{object_id}: missing artifacts {missing_artifacts}")
            if run_id and run_id not in run_ids:
                issues.append(f"{object_id}: missing run {run_id}")
            if evidence_required and not (sources or artifacts or run_id):
                issues.append(f"{object_id}: no source, artifact, or run evidence")

        if self.game is None:
            issues.append("game: missing structured Game object")
        elif self.game.id != self.game_id or self.game.title != self.game_title:
            issues.append("game: structured object disagrees with report identity")
        if self.play is None:
            issues.append("play: missing structured Play object")
        elif (
            self.play.id != self.system_id
            or self.play.slug != self.slug
            or self.play.title != self.system_title
        ):
            issues.append("play: structured object disagrees with report identity")
        if self.system_concept is None:
            issues.append("system_concept: missing structured SystemConcept object")
        else:
            if self.system_concept.id != self.system_id:
                issues.append("system_concept: id disagrees with system_id")
            check_refs(
                self.system_concept.id,
                self.system_concept.source_ids,
                self.system_concept.artifact_ids,
                self.system_concept.run_id,
            )
        if self.system_instance is None:
            issues.append("system_instance: missing structured SystemInstance object")
        else:
            if self.system_instance.concept_id != self.system_id:
                issues.append("system_instance: concept_id disagrees with system_id")
            if self.system_instance.build_scope_id != self.scope.id:
                issues.append("system_instance: build_scope_id disagrees with scope")
            for surface_id in sorted(set(self.system_instance.surface_ids) - surface_ids):
                issues.append(f"system_instance: missing surface {surface_id}")
            for run_id in sorted(set(self.system_instance.run_ids) - run_ids):
                issues.append(f"system_instance: missing run {run_id}")
            check_refs(
                self.system_instance.id,
                self.system_instance.source_ids,
                self.system_instance.artifact_ids,
                None,
                evidence_required=bool(not self.system_instance.run_ids),
            )
        if self.resource_model is None:
            issues.append("resource_model: missing structured ResourceModel object")
        else:
            for source_id in sorted(set(self.resource_model.source_ids) - source_ids):
                issues.append(f"{self.resource_model.id}: missing source {source_id}")
            for relation_id in sorted(set(self.resource_model.relation_ids) - relation_ids):
                issues.append(f"{self.resource_model.id}: missing relation {relation_id}")
            for resource in self.resource_model.resources:
                check_refs(
                    resource.id,
                    resource.source_ids,
                    resource.artifact_ids,
                    resource.run_id,
                )

        if self.summary_claim is None:
            issues.append("summary_claim: summary has no explicit provenance")
        else:
            if self.summary_claim.statement != self.summary:
                issues.append("summary_claim: statement disagrees with summary")
            check_refs(
                self.summary_claim.id,
                self.summary_claim.source_ids,
                self.summary_claim.artifact_ids,
                self.summary_claim.run_id,
            )

        check_refs(
            self.scope.id,
            self.scope.source_ids,
            self.scope.artifact_ids,
            self.scope.run_id,
        )
        if self.cover_artifact_id and self.cover_artifact_id not in artifact_ids:
            issues.append(f"cover: missing artifact {self.cover_artifact_id}")
        for run in self.runs:
            for artifact_id in sorted(set(run.artifact_ids) - artifact_ids):
                issues.append(f"{run.id}: missing artifact {artifact_id}")
        for artifact in self.artifacts:
            if artifact.run_id and artifact.run_id not in run_ids:
                issues.append(f"{artifact.id}: missing run {artifact.run_id}")
        for surface in self.surfaces:
            check_refs(surface.id, surface.source_ids, surface.artifact_ids, surface.run_id)
            for element in surface.elements:
                check_refs(element.id, element.source_ids, element.artifact_ids, None)
        for node in self.flow:
            check_refs(node.id, node.source_ids, node.artifact_ids, node.run_id)
            for surface_id in sorted(set(node.surface_ids) - surface_ids):
                issues.append(f"{node.id}: missing surface {surface_id}")
            for next_id in sorted(set(node.next) - flow_ids):
                issues.append(f"{node.id}: missing next flow node {next_id}")
        for mechanism in self.mechanisms:
            check_refs(
                mechanism.id,
                mechanism.source_ids,
                mechanism.artifact_ids,
                mechanism.run_id,
            )
        for relation in self.resources:
            check_refs(
                relation.id,
                relation.source_ids,
                relation.artifact_ids,
                relation.run_id,
            )
        for voice in self.player_voices:
            check_refs(voice.id, [voice.source_id], [], None)
            if voice.system_node_id and voice.system_node_id not in flow_ids:
                issues.append(f"{voice.id}: missing system node {voice.system_node_id}")
        for item in self.community_feedback:
            check_refs(item.id, [item.source.id], [], None)
        for claim in self.claims:
            check_refs(claim.id, claim.source_ids, claim.artifact_ids, claim.run_id)
        for observation in self.observations:
            if isinstance(observation, str):
                issues.append("observation: legacy string has no object-level provenance")
            else:
                check_refs(
                    observation.id,
                    observation.source_ids,
                    observation.artifact_ids,
                    observation.run_id,
                )
        for interpretation in self.interpretations:
            if isinstance(interpretation, str):
                issues.append("interpretation: legacy string has no object-level provenance")
            else:
                if interpretation.kind != ContentKind.analyst_interpretation:
                    issues.append(f"{interpretation.id}: interpretation must use analyst_interpretation")
                check_refs(
                    interpretation.id,
                    interpretation.source_ids,
                    interpretation.artifact_ids,
                    interpretation.run_id,
                )
        return sorted(set(issues))


class TargetInfo(BaseModel):
    id: str
    kind: Literal["adb", "mumu", "pc", "unity", "minecraft", "fixture", "remote_outbound"]
    label: str
    status: Literal["online", "offline", "unknown"]
    capabilities: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class TargetRecord(BaseModel):
    id: str
    provider: str
    endpoint: str
    kind: Literal["adb", "mumu", "pc", "unity", "minecraft", "fixture", "remote_outbound"]
    label: str
    status: Literal["online", "offline", "unknown"]
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    discovered_at: str = Field(default_factory=utc_now)
    last_seen_at: str | None = None


class DeviceLease(BaseModel):
    id: str
    target_id: str
    holder: str
    token: str
    acquired_at: str = Field(default_factory=utc_now)
    expires_at: str
    status: Literal["active", "released", "expired"] = "active"
    released_at: str | None = None
    owner_context: dict[str, Any] = Field(default_factory=dict)


class GatewayControl(BaseModel):
    target_id: str
    emergency_stopped: bool = False
    reason: str | None = None
    actor: str | None = None
    max_actions_per_minute: int = Field(default=30, ge=1, le=600)
    min_action_interval_ms: int = Field(default=150, ge=0, le=60_000)
    updated_at: str = Field(default_factory=utc_now)


class CaptureSession(BaseModel):
    id: str
    target_id: str
    status: Literal["running", "passed", "failed", "stopped"]
    started_at: str = Field(default_factory=utc_now)
    ended_at: str | None = None
    requested_frames: int = Field(ge=1, le=600)
    frame_artifact_ids: list[str] = Field(default_factory=list)
    ui_tree_artifact_ids: list[str] = Field(default_factory=list)
    recovery_count: int = 0
    error: str | None = None


class ReportPatchOperation(BaseModel):
    op: Literal["add", "replace", "remove"]
    target_kind: Literal[
        "report",
        "flow",
        "mechanism",
        "resource",
        "claim",
        "observation",
        "interpretation",
        "source",
        "voice",
        "surface",
    ]
    target_id: str | None = None
    field: str | None = None
    value: Any | None = None


class ReportPatch(BaseModel):
    id: str
    report_id: str
    base_revision: int = Field(ge=1)
    author: str
    note: str
    operations: list[ReportPatchOperation] = Field(min_length=1)
    status: Literal["proposed", "applied", "rejected"] = "proposed"
    created_at: str = Field(default_factory=utc_now)
    applied_at: str | None = None
    applied_revision: int | None = None
    rejected_at: str | None = None
    rejection_reason: str | None = None


class ReportAnnotation(BaseModel):
    id: str
    report_id: str
    object_id: str
    author: str
    body: str
    kind: Literal["comment", "correction", "question", "source_note"] = "comment"
    source_ids: list[str] = Field(default_factory=list)
    status: Literal["active", "resolved"] = "active"
    created_at: str = Field(default_factory=utc_now)
    resolved_at: str | None = None
    resolved_by: str | None = None


class NormalizedAction(BaseModel):
    type: Literal[
        "tap", "swipe", "pinch", "two_finger_swipe", "text", "key", "launch",
        "force_stop", "wait", "back", "home",
        "mouse_move", "mouse_button", "gamepad_button", "gamepad_axis", "reset",
    ]
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    duration_ms: int = 250
    text: str | None = None
    keycode: int | None = None
    package: str | None = None
    seconds: float = 0.5
    button: str | None = None
    pressed: bool | None = None
    axis: str | None = None
    value: float | None = Field(default=None, ge=-1, le=1)
    pinch_direction: Literal["in", "out"] | None = None
    pinch_percent: float | None = Field(default=None, gt=0, le=1)
    pinch_steps: int = Field(default=5, ge=2, le=60)
    two_finger_offset_x: int = Field(default=0, ge=-4096, le=4096)
    two_finger_offset_y: int = Field(default=50, ge=-4096, le=4096)
    two_finger_steps: int = Field(default=5, ge=2, le=60)

    @model_validator(mode="after")
    def validate_multitouch(self) -> NormalizedAction:
        if self.type in LIFECYCLE_ACTION_TYPES and (
            not self.package or not re.fullmatch(r"[a-zA-Z0-9_.]+", self.package)
        ):
            raise ValueError(f"{self.type} requires a safe package name")
        if self.type == "pinch":
            if self.x is None or self.y is None:
                raise ValueError("pinch requires center x/y")
            if self.pinch_direction is None:
                raise ValueError("pinch requires pinch_direction")
            if self.pinch_percent is None:
                raise ValueError("pinch requires pinch_percent")
        if self.type == "two_finger_swipe":
            if None in (self.x, self.y, self.x2, self.y2):
                raise ValueError("two_finger_swipe requires x/y/x2/y2")
            if self.two_finger_offset_x == 0 and self.two_finger_offset_y == 0:
                raise ValueError("two_finger_swipe requires a non-zero second-finger offset")
        return self


class SourcePixelPoint(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class SourcePixelRect(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def contains(self, point: SourcePixelPoint) -> bool:
        return (
            self.x <= point.x < self.x + self.width
            and self.y <= point.y < self.y + self.height
        )


class EvidenceDynamicSceneProfile(BaseModel):
    """Explicit bounded acceptance policy for terminals with continuous motion."""

    kind: Literal["bounded-motion-terminal"] = "bounded-motion-terminal"
    max_inlier_frame_distance: float = Field(default=0.10, gt=0, le=0.12)
    analysis_window_frames: int = Field(default=5, ge=1, le=12)
    required_inlier_ratio: float = Field(default=0.80, ge=0.66, le=1)


class EvidenceStability(BaseModel):
    method: Literal["perceptual-frame-distance"] = "perceptual-frame-distance"
    profile: Literal[
        "static-consecutive",
        "bounded-motion-terminal",
        "trusted-reference-or-static",
    ] = "static-consecutive"
    threshold: float = Field(default=0.01, ge=0, le=1)
    required_consecutive: int = Field(default=2, ge=1, le=20)
    observed_consecutive: int = Field(default=0, ge=0)
    final_distance: float | None = Field(default=None, ge=0, le=1)
    sample_distances: list[float] = Field(default_factory=list)
    sampled_frames: int = Field(default=0, ge=0)
    waited_seconds: float = Field(default=0, ge=0)
    dynamic_scene_profile: EvidenceDynamicSceneProfile | None = None
    analysis_window_distances: list[float] = Field(default_factory=list)
    analysis_inlier_count: int = Field(default=0, ge=0)
    analysis_required_inliers: int = Field(default=0, ge=0)
    trusted_reference_artifact_id: str | None = Field(default=None, min_length=1)
    trusted_reference_max_distance: float | None = Field(default=None, gt=0, le=1)
    trusted_reference_distance: float | None = Field(default=None, ge=0, le=1)
    trusted_reference_matched: bool = False
    settled: bool = False

    @model_validator(mode="after")
    def dynamic_profile_matches(self) -> "EvidenceStability":
        dynamic = self.profile == "bounded-motion-terminal"
        if dynamic != (self.dynamic_scene_profile is not None):
            raise ValueError(
                "bounded-motion-terminal stability requires exactly one dynamic scene profile"
            )
        trusted = self.profile == "trusted-reference-or-static"
        if trusted != (
            self.trusted_reference_artifact_id is not None
            and self.trusted_reference_max_distance is not None
        ):
            raise ValueError(
                "trusted-reference-or-static stability requires a reference artifact and threshold"
            )
        if self.trusted_reference_matched and (
            not trusted
            or self.trusted_reference_distance is None
            or self.trusted_reference_distance > self.trusted_reference_max_distance
        ):
            raise ValueError("trusted reference match is inconsistent with its distance")
        return self


class EvidenceTerminalCondition(BaseModel):
    """Optional, game-agnostic visual predicates for accepting an action terminal."""

    min_observation_seconds: float = Field(default=0, ge=0, le=60)
    min_visual_change_from_before: float | None = Field(default=None, gt=0, le=1)
    visual_reference_artifact_id: str | None = Field(default=None, min_length=1)
    max_visual_distance_from_reference: float = Field(default=0.03, ge=0, le=1)
    region: SourcePixelRect | None = None

    @model_validator(mode="after")
    def require_semantic_visual_predicate(self) -> "EvidenceTerminalCondition":
        if (
            self.min_visual_change_from_before is None
            and self.visual_reference_artifact_id is None
        ):
            raise ValueError(
                "terminal condition requires min_visual_change_from_before or "
                "visual_reference_artifact_id"
            )
        return self


class EvidenceTerminalEvaluation(BaseModel):
    condition: EvidenceTerminalCondition
    passed: bool = False
    observed_seconds: float = Field(default=0, ge=0)
    visual_change_from_before: float | None = Field(default=None, ge=0, le=1)
    visual_distance_from_reference: float | None = Field(default=None, ge=0, le=1)
    issues: list[str] = Field(default_factory=list)


class EvidenceTargetEffectEvaluation(BaseModel):
    """Auditable local effect measured around the action target."""

    target_bounds: SourcePixelRect
    evaluation_bounds: SourcePixelRect
    padding_pixels: int = Field(ge=0)
    visual_distance: float = Field(ge=0, le=1)
    structural_distance: float = Field(ge=0, le=1)
    min_visual_distance: float = Field(ge=0, le=1)
    min_structural_distance: float = Field(ge=0, le=1)
    passed: bool

    @model_validator(mode="after")
    def passed_matches_metrics(self) -> "EvidenceTargetEffectEvaluation":
        expected = (
            self.visual_distance >= self.min_visual_distance
            and self.structural_distance >= self.min_structural_distance
        )
        if self.passed != expected:
            raise ValueError("target effect verdict must match its visual and structural metrics")
        return self


class EvidenceLiveEvaluation(BaseModel):
    """Durable visual-effect evaluation of the caller's declared expectation."""

    expectation_met: bool
    stop_recommended: bool
    visual_distance: float = Field(ge=0, le=1)
    expected_min_visual_distance: float = Field(ge=0, le=1)
    elapsed_seconds: float = Field(ge=0)
    runtime_limit_seconds: int = Field(gt=0)
    global_visual_distance: float | None = Field(default=None, ge=0, le=1)
    evaluation_region: SourcePixelRect | None = None
    target_effect: EvidenceTargetEffectEvaluation | None = None
    evaluation_source: Literal[
        "legacy_global_visual_distance",
        "primary_visual_distance",
        "target_context_visual_and_structure",
        "visual_no_change",
        "unmet",
    ] = "legacy_global_visual_distance"
    effect_scope: Literal["visual_state_change_only"] = "visual_state_change_only"
    evaluator_version: str = Field(default="legacy-global-visual.v1", min_length=1)

    @model_validator(mode="after")
    def stop_matches_expectation(self) -> "EvidenceLiveEvaluation":
        if self.stop_recommended == self.expectation_met:
            raise ValueError("stop_recommended must be the inverse of expectation_met")
        if self.evaluation_source == "target_context_visual_and_structure" and (
            self.target_effect is None or not self.target_effect.passed
        ):
            raise ValueError("target-context source requires a passed target effect")
        if self.evaluation_source in {
            "primary_visual_distance",
            "target_context_visual_and_structure",
            "visual_no_change",
        } and not self.expectation_met:
            raise ValueError("a successful evaluation source requires expectation_met=true")
        if self.evaluation_source == "unmet" and self.expectation_met:
            raise ValueError("unmet evaluation source requires expectation_met=false")
        return self


class EvidenceStepAdjudication(BaseModel):
    step_id: str = Field(min_length=1)
    step_index: int = Field(ge=1)
    verdict: Literal[
        "valid",
        "mislabelled",
        "invalid_context",
        "facility_failure",
        "verified_no_change",
        "needs_review",
    ]
    corrected_target_name: str | None = None
    actual_from_state: str | None = None
    actual_to_state: str | None = None
    note: str = Field(min_length=1, max_length=4000)
    artifact_ids: list[str] = Field(default_factory=list)
    reviewer: str = Field(min_length=1)
    reviewed_at: str = Field(default_factory=utc_now)


class EvidenceAdjudicationLedger(BaseModel):
    schema_id: Literal["game-observatory.evidence-adjudications.v1"] = Field(
        default="game-observatory.evidence-adjudications.v1",
        alias="schema",
    )
    evidence_run_id: str = Field(min_length=1)
    items: list[EvidenceStepAdjudication] = Field(default_factory=list)


class EvidenceStep(BaseModel):
    id: str
    evidence_run_id: str
    step_index: int = Field(ge=1)
    status: Literal["running", "passed", "failed", "stopped"] = "running"
    started_at: str = Field(default_factory=utc_now)
    ended_at: str | None = None
    before_frame_id: str | None = None
    before_ui_tree_id: str | None = None
    action: NormalizedAction
    action_run_id: str | None = None
    action_started_at: str | None = None
    action_ended_at: str | None = None
    target_name: str | None = None
    source_point: SourcePixelPoint | None = None
    source_end_point: SourcePixelPoint | None = None
    target_bounds: SourcePixelRect | None = None
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    intermediate_frame_ids: list[str] = Field(default_factory=list)
    after_frame_id: str | None = None
    after_ui_tree_id: str | None = None
    video_artifact_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    observation_run_ids: list[str] = Field(default_factory=list)
    stability: EvidenceStability = Field(default_factory=EvidenceStability)
    terminal_condition: EvidenceTerminalCondition | None = None
    terminal_evaluation: EvidenceTerminalEvaluation | None = None
    live_evaluation: EvidenceLiveEvaluation | None = None
    quality_issues: list[str] = Field(default_factory=list)
    quality_advisories: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def publication_issues(self) -> list[str]:
        issues = list(self.quality_issues)
        if self.status != "passed":
            issues.append(f"{self.id}: step status is {self.status}")
        if not self.ended_at:
            issues.append(f"{self.id}: step has no end timestamp")
        required = {
            "before_frame": self.before_frame_id,
            "after_frame": self.after_frame_id,
        }
        if not self.metadata.get("observation_only"):
            required["action_run"] = self.action_run_id
        if self.metadata.get("capture_profile") != "compact_static":
            required["video"] = self.video_artifact_id
        for role, artifact_id in required.items():
            if not artifact_id:
                issues.append(f"{self.id}: missing {role}")
        if not self.stability.settled:
            issues.append(f"{self.id}: after state did not settle")
        if self.terminal_condition and (
            not self.terminal_evaluation or not self.terminal_evaluation.passed
        ):
            issues.append(f"{self.id}: declared terminal condition was not satisfied")
        for artifact_id in (
            [self.before_frame_id, self.after_frame_id, self.video_artifact_id]
            + self.intermediate_frame_ids
        ):
            if artifact_id and artifact_id not in self.artifact_ids:
                issues.append(f"{self.id}: {artifact_id} is absent from artifact_ids")
        if self.action.type == "tap":
            if not self.target_name:
                issues.append(f"{self.id}: tap has no human-readable target name")
            if not self.source_point:
                issues.append(f"{self.id}: tap has no source-pixel point")
            if not self.target_bounds:
                issues.append(f"{self.id}: tap has no source-pixel target bounds")
            if self.source_point and self.target_bounds and not self.target_bounds.contains(
                self.source_point
            ):
                issues.append(f"{self.id}: tap point is outside target bounds")
        if self.action.type == "swipe" and (
            not self.source_point or not self.source_end_point
        ):
            issues.append(f"{self.id}: swipe has no complete source-pixel path")
        if self.action.type == "pinch":
            if not self.target_name:
                issues.append(f"{self.id}: pinch has no human-readable target name")
            if not self.source_point:
                issues.append(f"{self.id}: pinch has no source-pixel center")
            if not self.target_bounds:
                issues.append(f"{self.id}: pinch has no source-pixel target bounds")
            if self.source_point and self.target_bounds and not self.target_bounds.contains(
                self.source_point
            ):
                issues.append(f"{self.id}: pinch center is outside target bounds")
        if self.action.type == "two_finger_swipe":
            if not self.target_name:
                issues.append(
                    f"{self.id}: two_finger_swipe has no human-readable target name"
                )
            if not self.source_point or not self.source_end_point:
                issues.append(
                    f"{self.id}: two_finger_swipe has no complete source-pixel path"
                )
            if not self.target_bounds:
                issues.append(
                    f"{self.id}: two_finger_swipe has no source-pixel target bounds"
                )
            if self.source_point and self.source_end_point:
                gesture_points = {
                    "first-finger start": self.source_point,
                    "first-finger end": self.source_end_point,
                    "second-finger start": SourcePixelPoint(
                        x=self.source_point.x + self.action.two_finger_offset_x,
                        y=self.source_point.y + self.action.two_finger_offset_y,
                    )
                    if self.source_point.x + self.action.two_finger_offset_x >= 0
                    and self.source_point.y + self.action.two_finger_offset_y >= 0
                    else None,
                    "second-finger end": SourcePixelPoint(
                        x=self.source_end_point.x + self.action.two_finger_offset_x,
                        y=self.source_end_point.y + self.action.two_finger_offset_y,
                    )
                    if self.source_end_point.x + self.action.two_finger_offset_x >= 0
                    and self.source_end_point.y + self.action.two_finger_offset_y >= 0
                    else None,
                }
                for label, point in gesture_points.items():
                    if point is None or not (
                        0 <= point.x < self.viewport_width
                        and 0 <= point.y < self.viewport_height
                    ):
                        issues.append(
                            f"{self.id}: two_finger_swipe {label} is outside viewport"
                        )
                    elif self.target_bounds and not self.target_bounds.contains(point):
                        issues.append(
                            f"{self.id}: two_finger_swipe {label} is outside target bounds"
                        )
        for role, point in (
            ("start", self.source_point),
            ("end", self.source_end_point),
        ):
            if point and not (
                0 <= point.x < self.viewport_width and 0 <= point.y < self.viewport_height
            ):
                issues.append(f"{self.id}: {role} point is outside viewport")
        return list(dict.fromkeys(issues))


class EvidenceRun(BaseModel):
    id: str
    target_id: str
    adapter: str
    status: Literal["running", "paused", "passed", "failed", "stopped"] = "running"
    game_id: str | None = None
    build_scope_id: str | None = None
    scope_id: str | None = None
    viewport_width: int = Field(gt=0)
    viewport_height: int = Field(gt=0)
    orientation: Literal["portrait", "landscape", "square"]
    environment: dict[str, Any] = Field(default_factory=dict)
    started_at: str = Field(default_factory=utc_now)
    ended_at: str | None = None
    step_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    action_run_ids: list[str] = Field(default_factory=list)
    observation_run_ids: list[str] = Field(default_factory=list)
    manifest_id: str | None = None
    error: str | None = None


class EvidenceRunManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    schema_id: Literal["game-observatory.evidence-manifest.v1"] = Field(
        default="game-observatory.evidence-manifest.v1",
        alias="schema",
    )
    id: str
    evidence_run_id: str
    generated_at: str = Field(default_factory=utc_now)
    run: EvidenceRun
    steps: list[EvidenceStep]
    artifact_ids: list[str]
    action_run_ids: list[str]
    observation_run_ids: list[str]
    publication_issues: list[str] = Field(default_factory=list)
    publishable: bool = False


class ObservationBundle(BaseModel):
    target_id: str
    captured_at: str = Field(default_factory=utc_now)
    frame: ArtifactRef
    ui_tree: ArtifactRef | None = None
    runtime_state: ArtifactRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    id: str
    adapter: str
    target_id: str
    task_id: str | None = None
    status: Literal["running", "passed", "failed", "stopped"]
    started_at: str = Field(default_factory=utc_now)
    ended_at: str | None = None
    checks: list[ObjectiveCheck] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None
