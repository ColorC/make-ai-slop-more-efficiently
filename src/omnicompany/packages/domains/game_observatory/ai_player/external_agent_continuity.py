"""Machine contracts for the public AI-player CLI and continuous external agents.

The contract is deliberately generated from typed data.  The future Click command tree,
external-agent wrappers, help text, and AFKJ comparison harness must consume these artifacts
instead of maintaining their own copies of the facility rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


MutationScope = Literal["read_only", "local_state", "device", "account"]
GuardProfile = Literal["none", "local_write", "device_preflight", "account_policy"]
CommandCategory = Literal[
    "诊断",
    "上下文",
    "环境",
    "设备",
    "会话",
    "观察",
    "操作",
    "探索",
    "任务",
    "玩法",
    "状态",
    "记忆",
    "技能",
    "攻略",
    "日账",
    "证据",
    "基准",
    "账号",
    "控制台",
]


class PlayerCLICommandV1(_StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    path: str = Field(min_length=1)
    category: CommandCategory
    summary: str = Field(min_length=1, max_length=200)
    mutation_scope: MutationScope
    guard_profile: GuardProfile
    json_output: bool = True
    examples: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_effect_guard(self) -> "PlayerCLICommandV1":
        if not self.path.startswith("omni game player"):
            raise ValueError("every public command must live below omni game player")
        required = {
            "read_only": "none",
            "local_state": "local_write",
            "device": "device_preflight",
            "account": "account_policy",
        }[self.mutation_scope]
        if self.guard_profile != required:
            raise ValueError(f"{self.mutation_scope} requires guard profile {required}")
        return self


class ExternalAgentProviderV1(_StrictModel):
    provider: Literal["codex-cli", "claude-code-cli"]
    executable: Literal["codex", "claude"]
    start_template: list[str] = Field(min_length=1)
    resume_template: list[str] = Field(min_length=1)
    event_format: Literal["jsonl", "stream-json"]
    session_id_source: str = Field(min_length=1)
    effort_argument: str = Field(min_length=1)

    @field_validator("start_template", "resume_template")
    @classmethod
    def require_placeholders(cls, value: list[str], info: Any) -> list[str]:
        joined = " ".join(value)
        required = {"{model}", "{effort}", "{prompt}"}
        if info.field_name == "resume_template":
            required.add("{session_id}")
        missing = sorted(placeholder for placeholder in required if placeholder not in joined)
        if missing:
            raise ValueError(f"{info.field_name} is missing placeholders: {missing}")
        return value


class AgentLayerV1(_StrictModel):
    tier: Literal["A0", "A1", "A2", "A3", "A4"]
    name: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    candidates: list[str] = Field(min_length=1)
    escalate_when: list[str] = Field(default_factory=list)


class ContinuousSessionPolicyV1(_StrictModel):
    semantic_actions_per_session_min: int = Field(default=10, ge=1)
    semantic_actions_per_session_target: int = Field(default=30, ge=1)
    heartbeat_after_device_action: bool = True
    heartbeat_interval_seconds_max: int = Field(default=30, ge=1)
    warn_after_silence_seconds: int = Field(default=30, ge=1)
    safe_stop_after_silence_seconds: int = Field(default=60, ge=1)
    checkpoint_every_semantic_actions: int = Field(default=10, ge=1)
    full_help_load_events: list[str] = Field(min_length=1)
    allowed_restart_reasons: list[str] = Field(min_length=1)
    forbidden_behaviors: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timing_and_span(self) -> "ContinuousSessionPolicyV1":
        if self.semantic_actions_per_session_target < self.semantic_actions_per_session_min:
            raise ValueError("target action span must be at least the minimum")
        if self.warn_after_silence_seconds > self.safe_stop_after_silence_seconds:
            raise ValueError("warning must precede safe stop")
        return self


class PlayerTestPolicyV1(_StrictModel):
    daily_test_limit_seconds: int = Field(default=120, ge=1, le=120)
    run_is_primary_e2e: bool = True
    required_pre_run_fields: list[str] = Field(min_length=1)
    required_post_run_fields: list[str] = Field(min_length=1)
    stop_and_diagnose_when: list[str] = Field(min_length=1)


class PlayerFacilityContractV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.facility-contract.v1"] = Field(
        default="game-observatory.ai-player.facility-contract.v1",
        alias="schema",
    )
    public_root: Literal["omni game player"] = "omni game player"
    help_language: Literal["zh-CN"] = "zh-CN"
    commands: list[PlayerCLICommandV1] = Field(min_length=1)
    external_agent_providers: list[ExternalAgentProviderV1] = Field(min_length=2)
    agent_layers: list[AgentLayerV1] = Field(min_length=5, max_length=5)
    continuous_session_policy: ContinuousSessionPolicyV1
    test_policy: PlayerTestPolicyV1
    invariants: list[str] = Field(min_length=1)
    canonical_sources: list[str] = Field(min_length=1)
    cli_help_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_schema_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    behavior_rules_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_contract(self) -> "PlayerFacilityContractV1":
        command_ids = [item.id for item in self.commands]
        command_paths = [item.path for item in self.commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("command ids must be unique")
        if len(command_paths) != len(set(command_paths)):
            raise ValueError("command paths must be unique")
        if [item.tier for item in self.agent_layers] != ["A0", "A1", "A2", "A3", "A4"]:
            raise ValueError("agent layers must contain A0 through A4 in order")
        if {item.provider for item in self.external_agent_providers} != {
            "codex-cli",
            "claude-code-cli",
        }:
            raise ValueError("both Codex CLI and Claude Code CLI providers are required")
        expected = facility_contract_sha256(self)
        if self.facility_contract_sha256 != expected:
            raise ValueError("facility contract sha256 does not match its canonical payload")
        return self


RestartReason = Literal[
    "phase_complete",
    "provider_failover",
    "hard_reset",
    "environment_identity_change",
    "facility_contract_change",
    "unrecoverable_context_pollution",
    "benchmark_isolation",
]


class ExternalAgentContinuousSessionV1(_StrictModel):
    schema_id: Literal["game-observatory.ai-player.external-agent-session.v1"] = Field(
        default="game-observatory.ai-player.external-agent-session.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    provider: Literal["codex-cli", "claude-code-cli"]
    model_selector: str = Field(min_length=1)
    resolved_model_id: str | None = Field(default=None, min_length=1)
    requested_effort: Literal["medium", "high"]
    actual_effort: Literal["medium", "high", "unsupported", "unreported"]
    permission_mode: Literal["readonly", "workspace-write", "trusted-bypass"]
    external_session_id: str | None = Field(default=None, min_length=1)
    environment_id: str = Field(min_length=1)
    phase_id: str = Field(min_length=1)
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation: int = Field(default=1, ge=1)
    previous_session_id: str | None = Field(default=None, min_length=1)
    restart_reason: RestartReason | None = None
    status: Literal["starting", "active", "suspended", "closed", "failed"] = "starting"
    invocation_count: int = Field(default=0, ge=0)
    semantic_action_count: int = Field(default=0, ge=0)
    atomic_action_count: int = Field(default=0, ge=0)
    observation_count: int = Field(default=0, ge=0)
    heartbeat_sequence: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_duration_seconds: float = Field(default=0, ge=0)
    task_ids: list[str] = Field(default_factory=list)
    checkpoint_id: str | None = Field(default=None, min_length=1)
    last_invocation_id: str | None = Field(default=None, min_length=1)
    last_error: str | None = Field(default=None, min_length=1)
    started_at: str = Field(min_length=1)
    last_heartbeat_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_continuity(self) -> "ExternalAgentContinuousSessionV1":
        restarted = self.generation > 1
        if restarted != bool(self.previous_session_id and self.restart_reason):
            raise ValueError(
                "only a replacement generation may declare previous_session_id and restart_reason"
            )
        if self.atomic_action_count < self.semantic_action_count:
            raise ValueError("atomic action count cannot be lower than semantic action count")
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("task ids must be unique")
        if self.status in {"active", "suspended", "closed"} and not self.external_session_id:
            raise ValueError("a usable external session requires its provider session id")
        if self.invocation_count == 0 and self.last_invocation_id is not None:
            raise ValueError("a session without invocations cannot reference a last invocation")
        if self.invocation_count > 0 and self.last_invocation_id is None:
            raise ValueError("a session with invocations requires its last invocation id")
        return self


class ExternalAgentBenchmarkCandidateV1(_StrictModel):
    id: str = Field(pattern=r"^[a-z0-9.-]+$")
    provider: Literal["codex-cli", "claude-code-cli"]
    requested_model_label: str = Field(min_length=1)
    cli_model_selector: str = Field(min_length=1)
    expected_model_family: str = Field(min_length=1)
    requested_effort: Literal["medium"] = "medium"
    resolved_model_id_required: bool = True
    actual_effort_required: bool = True


class ExternalAgentBenchmarkTaskV1(_StrictModel):
    id: Literal["B0", "B1", "B2", "B3", "B4", "B5"]
    name: str = Field(min_length=1)
    sample_count: int = Field(ge=1)
    description: str = Field(min_length=1)
    acceptance_focus: list[str] = Field(min_length=1)
    same_session_required: bool


class ExternalAgentBenchmarkThresholdV1(_StrictModel):
    known_target_accuracy_min: float = Field(default=0.95, ge=0, le=1)
    overlay_lower_layer_misclick_max: int = Field(default=0, ge=0)
    evidence_completeness_min: float = Field(default=1.0, ge=0, le=1)
    interruption_recovery_min: float = Field(default=1.0, ge=0, le=1)
    a1_warm_decision_p50_seconds_max: float = Field(default=15, gt=0)
    a1_warm_decision_p95_seconds_max: float = Field(default=30, gt=0)
    a2_warm_decision_p50_seconds_max: float = Field(default=30, gt=0)
    a2_warm_decision_p95_seconds_max: float = Field(default=60, gt=0)
    a2_continuous_semantic_actions_min: int = Field(default=30, ge=1)


class AFKJExternalAgentContinuityManifestV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.afkj-external-agent-continuity-manifest.v1"
    ] = Field(
        default="game-observatory.ai-player.afkj-external-agent-continuity-manifest.v1",
        alias="schema",
    )
    benchmark_id: Literal["afkj_external_agent_continuity_v1"] = (
        "afkj_external_agent_continuity_v1"
    )
    game_id: Literal["afk-journey"] = "afk-journey"
    purpose: str = Field(min_length=1)
    facility_contract_path: str = Field(min_length=1)
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: list[ExternalAgentBenchmarkCandidateV1] = Field(min_length=3, max_length=3)
    tasks: list[ExternalAgentBenchmarkTaskV1] = Field(min_length=6, max_length=6)
    fairness_controls: list[str] = Field(min_length=1)
    measured_fields: list[str] = Field(min_length=1)
    thresholds: ExternalAgentBenchmarkThresholdV1
    selection_rule: str = Field(min_length=1)
    required_artifacts: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_benchmark_shape(self) -> "AFKJExternalAgentContinuityManifestV1":
        if [item.id for item in self.tasks] != ["B0", "B1", "B2", "B3", "B4", "B5"]:
            raise ValueError("benchmark tasks must contain B0 through B5 in order")
        if {item.id for item in self.candidates} != {
            "claude-sonnet-5-medium",
            "gpt-5.6-terra-medium",
            "gpt-5.6-luna-medium",
        }:
            raise ValueError("the three requested medium-effort candidates are required")
        if not self.tasks[-1].same_session_required or self.tasks[-1].sample_count < 30:
            raise ValueError("B5 must require at least 30 actions in one external session")
        return self


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def facility_contract_sha256(contract: PlayerFacilityContractV1 | Mapping[str, Any]) -> str:
    if isinstance(contract, PlayerFacilityContractV1):
        payload = contract.model_dump(mode="json", by_alias=True)
    else:
        payload = dict(contract)
    payload.pop("facility_contract_sha256", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _command(
    command_id: str,
    suffix: str,
    category: CommandCategory,
    summary: str,
    scope: MutationScope = "read_only",
    example: str | None = None,
) -> PlayerCLICommandV1:
    guard: GuardProfile = {
        "read_only": "none",
        "local_state": "local_write",
        "device": "device_preflight",
        "account": "account_policy",
    }[scope]
    path = f"omni game player {suffix}".rstrip()
    return PlayerCLICommandV1(
        id=command_id,
        path=path,
        category=category,
        summary=summary,
        mutation_scope=scope,
        guard_profile=guard,
        examples=[example or f"{path} --help"],
    )


def build_player_facility_contract() -> PlayerFacilityContractV1:
    commands = [
        _command("doctor", "doctor", "诊断", "检查 CLI、数据库、设备桥、外部 Agent 与证据目录。"),
        _command(
            "acceptance.run",
            "acceptance run",
            "诊断",
            "以显式回执执行 fail-closed AI 玩家验收并保存结果。",
            "local_state",
        ),
        _command("context.export", "context export", "上下文", "导出一次加载的精简设施合同和阶段上下文。"),
        _command("environment.list", "environment list", "环境", "列出游戏、构建、账号、设备和区服环境。"),
        _command("environment.show", "environment show", "环境", "查看环境身份、别名、当前叶和来源。"),
        _command("environment.use", "environment use", "环境", "解析并返回唯一已验证环境叶。"),
        _command("environment.register", "environment register", "环境", "从在线模拟器当前画面登记不可变根环境。", "local_state"),
        _command("environment.promote", "environment promote", "环境", "用终态证据提升到更具体的账号或区服环境叶。", "local_state"),
        _command("device.list", "device list", "设备", "列出或刷新统一 CLI 可用的 ADB 与模拟器目标。", "local_state"),
        _command("device.inspect", "device inspect", "设备", "只读检查前台界面与游戏包构建身份。"),
        _command("session.start", "session start", "会话", "启动可续接的外部 Agent Session。", "local_state"),
        _command(
            "session.rollover",
            "session rollover",
            "会话",
            "阶段结束后建立新 canonical 代，并复用同一原生 provider Session。",
            "local_state",
        ),
        _command("session.resume", "session resume", "会话", "续接原外部 Session 并先重新观察。", "local_state"),
        _command("session.status", "session status", "会话", "查看模型、effort、心跳、检查点和动作计数。"),
        _command("session.heartbeat", "session heartbeat", "会话", "写入轻量心跳和当前阶段摘要。", "local_state"),
        _command("session.checkpoint", "session checkpoint", "会话", "保存任务、状态、记忆和副作用检查点。", "local_state"),
        _command("session.stop", "session stop", "会话", "安全停止设备计划并保留可恢复 Session。", "local_state"),
        _command("observe.capture", "observe capture", "观察", "采集当前画面、UI 树与第一次动作可引用的只读证据锚点。", "local_state"),
        _command("observe.inspect", "observe inspect", "观察", "解析画面、交互候选、遮罩和信息增量。"),
        _command(
            "observe.locate",
            "observe locate",
            "观察",
            "对 canonical 原图运行可缓存的 OmniParser 元素定位。",
            "local_state",
        ),
        _command("act.tap", "act tap", "操作", "按可追溯目标或坐标点击并核对前后状态。", "device"),
        _command(
            "act.tap-anchor",
            "act tap-anchor",
            "操作",
            "按已审阅状态锚点生成或执行受守卫点击；动态对象必须先视觉重定位。",
            "device",
        ),
        _command(
            "act.tap-preview",
            "act tap-preview",
            "操作",
            "按 agent preview 像素点击，由 CLI 换算原图坐标并复用动作守卫。",
            "device",
        ),
        _command(
            "act.tap-element",
            "act tap-element",
            "操作",
            "按持久化定位结果和 element ID 点击原图检测框中心。",
            "device",
        ),
        _command("act.swipe", "act swipe", "操作", "执行滑动并核对预期变化。", "device"),
        _command("act.back", "act back", "操作", "执行返回并核对关闭层级或状态迁移。", "device"),
        _command("act.launch", "act launch", "操作", "启动当前环境的游戏包并核对前台画面。", "device"),
        _command("act.text", "act text", "操作", "输入文本；账号发言仍经过作者口径策略。", "account"),
        _command("act.wait", "act wait", "操作", "等待有限异步变化并持续心跳。", "device"),
        _command("explore.plan", "explore plan", "探索", "基于前沿、任务和攻略生成阶段动作预算。", "local_state"),
        _command("explore.run", "explore run", "探索", "在一个连续 Session 内执行语义动作段。", "device"),
        _command("explore.drive", "explore drive", "探索", "复用同一外部 Session 连续运行到阶段停止条件。", "device"),
        _command("explore.dispatch", "explore dispatch", "探索", "将完整 drive 托管给独立后台 worker，保留原 Session 与全量日志。", "device"),
        _command("explore.dispatch-status", "explore dispatch-status", "探索", "只读查看后台阶段、心跳和退出状态，不调用模型或游戏。"),
        _command("explore.status", "explore status", "探索", "查看进度、空转、信息增量和降级状态。"),
        _command("explore.interrupt", "explore interrupt", "探索", "安全中断设备动作段并保留 Session。", "local_state"),
        _command("explore.resume", "explore resume", "探索", "从检查点继续同一探索阶段。", "device"),
        _command("task.list", "task list", "任务", "列出持续任务、前沿候选和优先级。"),
        _command("task.add", "task add", "任务", "建立可验证退出条件的任务。", "local_state"),
        _command("task.claim", "task claim", "任务", "认领任务，并可绑定到运行中的 Session。", "local_state"),
        _command("task.complete", "task complete", "任务", "以终态证据关闭任务。", "local_state"),
        _command("task.block", "task block", "任务", "记录已复现的真实阻断与恢复条件。", "local_state"),
        _command("gameplay.list", "gameplay list", "玩法", "列出玩法候选、边界、规则线索、相邻关系和来源证据。"),
        _command("gameplay.show", "gameplay show", "玩法", "查看一个玩法候选的完整边界与证据。"),
        _command(
            "gameplay.invalidate",
            "gameplay invalidate",
            "玩法",
            "保留历史与证据，追加一个自动发现不会覆盖的失效版本。",
            "local_state",
        ),
        _command(
            "gameplay.discover",
            "gameplay discover",
            "玩法",
            "有界扫描实机轨迹并幂等写入待审玩法候选；不调用模型或设备。",
            "local_state",
        ),
        _command("state.current", "state current", "状态", "查看当前语义状态及识别依据。"),
        _command("state.map", "state map", "状态", "查看已知界面、状态、转移和玩法归属。"),
        _command("state.route", "state route", "状态", "规划或核对到目标状态的最短可靠路线。"),
        _command(
            "navigate",
            "navigate",
            "技能",
            "按目标自动选择并执行已学习路径；动态画面用首控件局部守卫匹配入口，紧凑回执直接返回实际技能版本，未命中时在设备动作前交回语义探索。",
            "device",
            "omni game player navigate 编队换将 --environment <ENVIRONMENT_ID> "
            "--session <SESSION_ID> --source-step <SOURCE_STEP_ID>",
        ),
        _command(
            "state.review-export",
            "state review-export",
            "状态",
            "导出带来源哈希的独立语义状态审阅包，不改变 canonical 状态。",
            "local_state",
        ),
        _command(
            "state.review-sign",
            "state review-sign",
            "状态",
            "使用受信本地密钥签署独立审阅种子，不保存私钥材料。",
            "local_state",
        ),
        _command(
            "state.review-apply",
            "state review-apply",
            "状态",
            "原子应用签名状态裁决，并以原 EvidenceRun 重新推导转移结果。",
            "local_state",
        ),
        _command("memory.recall", "memory recall", "记忆", "按环境和任务召回焦点记忆。"),
        _command("memory.record", "memory record", "记忆", "追加有来源的情节、语义、程序或禁忌记忆。", "local_state"),
        _command("memory.consolidate", "memory consolidate", "记忆", "去重并固化阶段记忆，不丢失来源。", "local_state"),
        _command("skill.list", "skill list", "技能", "列出分层操作技能及适用环境。"),
        _command("skill.show", "skill show", "技能", "查看技能步骤、守卫、回退和验证记录。"),
        _command("skill.replay", "skill replay", "技能", "执行已验证技能并保留动作证据。", "device"),
        _command(
            "skill.health",
            "skill health",
            "技能",
            "读取操作级复用健康、当前任务与可直接复用候选，不访问设备。",
        ),
        _command(
            "skill.reconcile-operation-memory",
            "skill reconcile-operation-memory",
            "技能",
            "幂等补记有真实状态护栏与动作证据的 SkillRun；不访问设备。",
            "local_state",
        ),
        _command(
            "skill.confirm-terminal-alias",
            "skill confirm-terminal-alias",
            "技能",
            "确认安全中断后的实际终态是调用上下文造成的合法落点，并保留证据来源。",
            "local_state",
        ),
        _command(
            "skill.invalidate",
            "skill invalidate",
            "技能",
            "用终态截图反例停用语义目标错误的精确技能版本，并保留历史与来源。",
            "local_state",
        ),
        _command("skill.crystallize", "skill crystallize", "技能", "从成功轨迹归纳候选技能。", "local_state"),
        _command("skill.validate", "skill validate", "技能", "从独立 SkillRun 推导速度、精度和副作用验证门。", "local_state"),
        _command("guide.search", "guide search", "攻略", "检索已导入且保留时效和来源的攻略。"),
        _command("guide.import", "guide import", "攻略", "导入攻略结论、版本、时间和原始来源。", "local_state"),
        _command("guide.freshness", "guide freshness", "攻略", "判断攻略是否仍适用于当前环境。"),
        _command(
            "guide.refresh.submit",
            "guide refresh submit",
            "攻略",
            "在四类明确触发点提交异步时效攻略请求，立即返回且不等待研究。",
            "local_state",
        ),
        _command(
            "guide.refresh.pending",
            "guide refresh pending",
            "攻略",
            "列出尚无终态回执的攻略刷新请求。",
        ),
        _command(
            "guide.refresh.work-item",
            "guide refresh work-item",
            "攻略",
            "输出供既有 research.run 消费的严格工作合同。",
        ),
        _command(
            "guide.refresh.complete",
            "guide refresh complete",
            "攻略",
            "校验并保存独立研究 worker 产出的来源快照与攻略知识。",
            "local_state",
        ),
        _command(
            "guide.refresh.finish",
            "guide refresh finish",
            "攻略",
            "为离线、来源不可用或研究失败写入明确终态。",
            "local_state",
        ),
        _command(
            "guide.refresh.retry",
            "guide refresh retry",
            "攻略",
            "从离线、来源不可用或失败终态建立新尝试并保留旧回执。",
            "local_state",
        ),
        _command(
            "guide.refresh.contradict",
            "guide refresh contradict",
            "攻略",
            "用实机反例追加 contradicted 版本并保留原来源。",
            "local_state",
        ),
        _command("daily.schema", "daily schema", "日账", "查看三谋连续日职责候选与封账合同。"),
        _command("daily.status", "daily status", "日账", "只读查看当前自然日、下一职责与封账条件。"),
        _command("daily.advance", "daily advance", "日账", "用已保存的 canonical 证据推进当天下一项职责。", "local_state"),
        _command("daily.seal", "daily seal", "日账", "在六项职责与真实动作质量通过后封账。", "local_state"),
        _command("evidence.run", "evidence run", "证据", "查看动作段 EvidenceRun。"),
        _command("evidence.step", "evidence step", "证据", "查看 Before、Action、After 和点击标记。"),
        _command("evidence.show", "evidence show", "证据", "按稳定 ID 打开图像、视频帧和来源。"),
        _command("evidence.export", "evidence export", "证据", "导出玩法拆解需要的证据索引。", "local_state"),
        _command("evidence.prune", "evidence prune", "证据", "报告 canonical 重复 hash，并清理未登记孤儿文件。", "local_state"),
        _command("benchmark.list", "benchmark list", "基准", "列出 AFKJ 与持续 Session 基准。"),
        _command(
            "benchmark.run",
            "benchmark run",
            "基准",
            "运行 B0/B1 与 A2 新界面 B3 基准；B1/B3 中断后可按不可变 request 续跑。",
            "local_state",
            "omni game player benchmark run --candidate gpt-5.6-luna-medium "
            "--task B3 --case B3-01 --run-id afkj-b3-run-001；B1/B3 续跑追加 "
            "--resume-existing",
        ),
        _command("benchmark.compare", "benchmark compare", "基准", "比较质量门、时延、token 和信息增量。"),
        _command("account.status", "account status", "账号", "查看账号身份、区服和纯 AI 账号状态。"),
        _command("account.metric", "account metric", "账号", "查看有来源的账号指标及变化。"),
        _command(
            "account.metric-derive",
            "account metric-derive",
            "账号",
            "从同一终态证据的局部数值区域推导并保存任务相关账号指标。",
            "local_state",
        ),
        _command("account.policy", "account policy", "账号", "查看支付、身份资料和发言策略。"),
        _command("account.initialize", "account initialize", "账号", "初始化游戏内自主、支付和外部身份资料的固定权限边界。", "local_state"),
        _command("console.serve", "console serve", "控制台", "启动独立 AI 玩家控制台。", "local_state"),
    ]
    providers = [
        ExternalAgentProviderV1(
            provider="codex-cli",
            executable="codex",
            start_template=[
                "codex", "exec", "-C", "{workspace}", "-m", "{model}",
                "-c", 'model_reasoning_effort="{effort}"', "--json", "{prompt}",
            ],
            resume_template=[
                "codex", "exec", "resume", "-m", "{model}", "-c",
                'model_reasoning_effort="{effort}"', "--json", "{session_id}", "{prompt}",
            ],
            event_format="jsonl",
            session_id_source="thread.started.thread_id",
            effort_argument='-c model_reasoning_effort="{effort}"',
        ),
        ExternalAgentProviderV1(
            provider="claude-code-cli",
            executable="claude",
            start_template=[
                "claude", "-p", "--model", "{model}", "--effort", "{effort}",
                "--output-format", "stream-json", "--session-id", "{session_id}", "{prompt}",
            ],
            resume_template=[
                "claude", "-p", "--model", "{model}", "--effort", "{effort}",
                "--output-format", "stream-json", "--resume", "{session_id}", "{prompt}",
            ],
            event_format="stream-json",
            session_id_source="caller-assigned --session-id / result.session_id",
            effort_argument="--effort {effort}",
        ),
    ]
    layers = [
        AgentLayerV1(
            tier="A0", name="固定动作执行层",
            responsibility="ADB、模拟器、已验证技能和固定自动化负责快速原子动作。",
            candidates=["DeviceExecutor", "ADB", "MAA-compatible skills"],
            escalate_when=["状态守卫不匹配", "目标不可定位", "预期变化未发生"],
        ),
        AgentLayerV1(
            tier="A1", name="快速语义操作层",
            responsibility="用低时延语义模型选择已知目标和调用 A0。",
            candidates=["GPT-5.6 Luna medium", "AFKJ benchmark winner"],
            escalate_when=["出现新界面", "需要跨系统判断", "连续两次无效动作"],
        ),
        AgentLayerV1(
            tier="A2", name="持续游戏层",
            responsibility="在连续 Session 中规划、探索、理解玩法并提升账号。",
            candidates=["Claude Sonnet 5 medium", "GPT-5.6 Terra medium"],
            escalate_when=["需要复杂设施编程", "阶段目标需要重构", "高风险不可逆决策"],
        ),
        AgentLayerV1(
            tier="A3", name="设施工程层",
            responsibility="处理阶段规划和较复杂的自动化设施编程。",
            candidates=["Claude Opus", "GPT-5.6 Sol high"],
            escalate_when=["跨设施决策", "权限边界变化", "验收口径冲突"],
        ),
        AgentLayerV1(
            tier="A4", name="主 Agent 监督层",
            responsibility="维护目标、质量门、分层路由和最终审阅；必要时直接承担 A3。",
            candidates=["goal owner main agent"],
            escalate_when=[],
        ),
    ]
    payload: dict[str, Any] = {
        "schema": "game-observatory.ai-player.facility-contract.v1",
        "public_root": "omni game player",
        "help_language": "zh-CN",
        "commands": [item.model_dump(mode="json", by_alias=True) for item in commands],
        "external_agent_providers": [
            item.model_dump(mode="json", by_alias=True) for item in providers
        ],
        "agent_layers": [item.model_dump(mode="json", by_alias=True) for item in layers],
        "continuous_session_policy": ContinuousSessionPolicyV1(
            full_help_load_events=["session_start", "facility_contract_change"],
            allowed_restart_reasons=[
                "phase_complete", "provider_failover", "hard_reset",
                "environment_identity_change",
                "facility_contract_change", "unrecoverable_context_pollution",
                "benchmark_isolation",
            ],
            forbidden_behaviors=[
                "每个游戏动作创建一个新 Agent 或新 Session",
                "把单步证据边界误作 Agent 生命周期边界",
                "无心跳地长时间占用设备",
                "重复读取完整设施说明代替恢复焦点上下文",
            ],
        ).model_dump(mode="json", by_alias=True),
        "test_policy": PlayerTestPolicyV1(
            required_pre_run_fields=["目标", "当前状态", "预期变化", "动作预算", "停止条件"],
            required_post_run_fields=["实际变化", "证据索引", "信息增量", "副作用", "下一任务"],
            stop_and_diagnose_when=["连续 30 秒没有心跳", "同一动作重复两次无信息增量", "来源守卫失败"],
        ).model_dump(mode="json", by_alias=True),
        "invariants": [
            "每个实体动作都先检查目标、遮罩、来源和账号策略，再记录前后证据。",
            "A2 每轮第一张图由外层在调用前即时采集并作为权威当前帧，附加图片只作历史参考。",
            "观察、语义决策、固定自动化和设施编程按 A0 至 A4 分层路由。",
            "同一阶段优先续接原外部 Session；只有白名单原因允许新建。",
            "游戏运行本身是主要 E2E；日常定向测试总时长不得超过 120 秒。",
            "持续 Agent 每次确定下一界面或操作目标后先调用 navigate；命中已知路径时整段交给零模型输入的低级固定自动化并直接返回终态预览，未命中且设备未动作后才步进探索缺失片段；进入与退出目标不得互相模糊匹配。",
            "每条成功且已理解的交互路径当轮写入带来源的程序记忆；后续按目标直接选路，守卫失配时只把失败片段交回持续 Agent。",
            "已理解界面的程序守卫固定外框与控件结构，并允许同一界面内的数值、进度、倒计时、动画和地图动态内容变化；从这类界面首次探索新边时自动采用局部结构稳定终态，不能因待机动画漏记已经成功且理解的路径；弹窗和异页仍须拒绝。",
            "固定技能到达的实际界面与目标语义明确不符时，用终态截图证据停用精确版本；后续绕开错误版本并只重探该片段。",
            "资源不足、并发占用、冷却、解锁与次数限制等运行时前置失败只保留失败运行和终态证据，不停用技能；已停用版本不得由原始旧证据自动复活。",
            "技能声明终态只有在实机截图匹配时才可作为权威状态；不匹配的完整终态证据继续进入普通状态归纳，预期只保留为反例。",
            "外部 Session 成功返回后由包装器自动把终态动作 EvidenceRun 幂等沉淀进状态图。",
            "只保留变化帧、关键状态和必要上下文，避免重复静帧浪费磁盘与 token。",
            "攻略必须保留来源、时间、版本和适用环境；过期结论不能覆盖实机证据。",
            "除真实货币支付和外部个人身份资料外，纯 AI 账号可按任务自主行动。",
            "账号发言仅在必要时发生，并遵循作者口径。",
        ],
        "canonical_sources": [
            "docs/plans/game-observatory/[2026-07-15]AI-PLAYER-LIFELONG-EXPLORATION-v1/plan.md",
            "docs/plans/game-observatory/[2026-07-15]AI-PLAYER-LIFELONG-EXPLORATION-v1/acceptance.md",
            "docs/plans/game-observatory/[2026-07-15]AI-PLAYER-LIFELONG-EXPLORATION-v1/continuous-player-iteration-v1.zh-CN.md",
            "docs/plans/game-observatory/[2026-07-15]AI-PLAYER-LIFELONG-EXPLORATION-v1/external-agent-continuous-exploration-v1.zh-CN.md",
        ],
    }
    payload["cli_help_sha256"] = _canonical_sha256(
        {
            "public_root": payload["public_root"],
            "help_language": payload["help_language"],
            "commands": [
                {
                    "path": item["path"],
                    "summary": item["summary"],
                    "examples": item["examples"],
                    "mutation_scope": item["mutation_scope"],
                    "guard_profile": item["guard_profile"],
                }
                for item in payload["commands"]
            ],
        }
    )
    payload["public_schema_bundle_sha256"] = _canonical_sha256(
        {
            model.__name__: model.model_json_schema(by_alias=True)
            for model in (
                PlayerCLICommandV1,
                ExternalAgentProviderV1,
                AgentLayerV1,
                ContinuousSessionPolicyV1,
                PlayerTestPolicyV1,
                PlayerFacilityContractV1,
                ExternalAgentContinuousSessionV1,
            )
        }
    )
    payload["behavior_rules_sha256"] = _canonical_sha256(
        {
            "agent_layers": payload["agent_layers"],
            "continuous_session_policy": payload["continuous_session_policy"],
            "test_policy": payload["test_policy"],
            "invariants": payload["invariants"],
        }
    )
    payload["facility_contract_sha256"] = facility_contract_sha256(payload)
    return PlayerFacilityContractV1.model_validate(payload)


def build_afkj_external_agent_manifest(
    contract: PlayerFacilityContractV1 | None = None,
) -> AFKJExternalAgentContinuityManifestV1:
    facility = contract or build_player_facility_contract()
    candidates = [
        ExternalAgentBenchmarkCandidateV1(
            id="claude-sonnet-5-medium", provider="claude-code-cli",
            requested_model_label="Claude Sonnet 5", cli_model_selector="sonnet",
            expected_model_family="claude-sonnet-5",
        ),
        ExternalAgentBenchmarkCandidateV1(
            id="gpt-5.6-terra-medium", provider="codex-cli",
            requested_model_label="GPT-5.6 Terra", cli_model_selector="gpt-5.6-terra",
            expected_model_family="gpt-5.6-terra",
        ),
        ExternalAgentBenchmarkCandidateV1(
            id="gpt-5.6-luna-medium", provider="codex-cli",
            requested_model_label="GPT-5.6 Luna", cli_model_selector="gpt-5.6-luna",
            expected_model_family="gpt-5.6-luna",
        ),
    ]
    tasks = [
        ExternalAgentBenchmarkTaskV1(
            id="B0", name="冷启动设施理解", sample_count=10,
            description="新 Session 读取统一帮助和设施合同，完成十项理解检查。",
            acceptance_focus=["首个有效计划时延", "重复读取倾向", "设施路由正确性"],
            same_session_required=True,
        ),
        ExternalAgentBenchmarkTaskV1(
            id="B1", name="已知状态", sample_count=20,
            description="处理冻结 AFKJ 真值中的已知控件与遮罩样本。",
            acceptance_focus=["目标定位", "风险判断", "A0 路由"],
            same_session_required=True,
        ),
        ExternalAgentBenchmarkTaskV1(
            id="B2", name="已知路线", sample_count=8,
            description="连续执行八条已知目标路线并保留完整前后证据。",
            acceptance_focus=["路径效率", "技能复用", "证据完整"],
            same_session_required=True,
        ),
        ExternalAgentBenchmarkTaskV1(
            id="B3", name="保留新界面", sample_count=10,
            description="处理十个不在 warm memory 中的真实 AFKJ 画面。",
            acceptance_focus=["步进速度", "信息增量", "误点率"],
            same_session_required=True,
        ),
        ExternalAgentBenchmarkTaskV1(
            id="B4", name="中断恢复", sample_count=3,
            description="分别中断 Agent CLI、游戏进程和模拟器后恢复。",
            acceptance_focus=["Session 续接", "状态重观测", "非幂等副作用"],
            same_session_required=True,
        ),
        ExternalAgentBenchmarkTaskV1(
            id="B5", name="长段持续", sample_count=30,
            description="同一外部 Session 完成至少三十个语义动作。",
            acceptance_focus=["上下文退化", "空转", "逐动作重启"],
            same_session_required=True,
        ),
    ]
    return AFKJExternalAgentContinuityManifestV1(
        purpose="比较三个 medium 候选在持续游戏、快速操作、恢复和 token 效率上的表现。",
        facility_contract_path="config/game_observatory/player_facility_contract.v1.json",
        facility_contract_sha256=facility.facility_contract_sha256,
        candidates=candidates,
        tasks=tasks,
        fairness_controls=[
            "相同 AFKJ build 和账号快照", "相同设备与任务顺序", "相同设施合同哈希",
            "相同动作预算和证据要求", "记录实际 CLI 版本、返回模型 ID 和实际 effort",
        ],
        measured_fields=[
            "冷启动到首个有效计划、首个 CLI 调用和首个 EvidenceRun 的时延",
            "warm Session 相邻动作决策 P50 与 P95",
            "输入、输出、cache token 和每个有效动作 token",
            "目标准确率、误点、无效果动作、重复动作簇和任务完成率",
            "新状态、新转移、规则线索和玩法候选的信息增量",
            "中断恢复、Session 延续、检查点和副作用核对",
        ],
        thresholds=ExternalAgentBenchmarkThresholdV1(),
        selection_rule="先通过全部质量硬门，再按 A1 与 A2 角色分别比较速度和 token；不得以速度覆盖质量失败。",
        required_artifacts=[
            "原始外部 Agent 事件流", "模型与 effort 解析记录", "token 与时延明细",
            "EvidenceRun 和 EvidenceStep 索引", "中断恢复记录", "可重算同口径比较报告",
        ],
    )


def render_facility_help(contract: PlayerFacilityContractV1 | None = None) -> str:
    facility = contract or build_player_facility_contract()
    lines = [
        "用法: omni game player [选项] 命令 [参数]...",
        "",
        "本地 AI 玩家统一入口。所有写操作支持 --json，设备与账号操作自动执行守卫。",
        f"设施合同: {facility.facility_contract_sha256}",
        "",
        "命令:",
    ]
    categories: list[str] = []
    for command in facility.commands:
        if command.category not in categories:
            categories.append(command.category)
    for category in categories:
        lines.extend(["", f"  {category}"])
        for command in (item for item in facility.commands if item.category == category):
            suffix = command.path.removeprefix(f"{facility.public_root} ")
            lines.append(f"    {suffix:<24} {command.summary}")
    lines.extend(
        [
            "",
            "连续 Session:",
            "  同一阶段连续执行 10—30 个语义动作；每个动作仍独立保存前后证据。",
            "  暂停或 CLI 退出后优先 resume；只有合同列出的阶段或硬重启原因允许换 Session。",
            "",
            "进一步帮助:",
            "  omni game player <命令> --help",
            "  omni game player context export --json",
            "",
        ]
    )
    return "\n".join(lines)


def export_external_agent_contracts(repository_root: Path) -> tuple[Path, ...]:
    contract = build_player_facility_contract()
    manifest = build_afkj_external_agent_manifest(contract)
    outputs = {
        repository_root / "config/game_observatory/player_facility_contract.v1.json": _json_bytes(
            contract.model_dump(mode="json", by_alias=True)
        ),
        repository_root
        / "data/domains/game_observatory/benchmarks/ai_player/fixtures/"
        / "afkj_external_agent_continuity_v1.manifest.json": _json_bytes(
            manifest.model_dump(mode="json", by_alias=True)
        ),
        repository_root
        / "docs/plans/game-observatory/"
        / "[2026-07-15]AI-PLAYER-LIFELONG-EXPLORATION-v1/"
        / "omni-game-player-help.snapshot.txt": render_facility_help(contract).encode("utf-8"),
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return tuple(outputs)


def check_external_agent_contracts(repository_root: Path) -> tuple[Path, ...]:
    contract = build_player_facility_contract()
    manifest = build_afkj_external_agent_manifest(contract)
    expected = {
        repository_root / "config/game_observatory/player_facility_contract.v1.json": _json_bytes(
            contract.model_dump(mode="json", by_alias=True)
        ),
        repository_root
        / "data/domains/game_observatory/benchmarks/ai_player/fixtures/"
        / "afkj_external_agent_continuity_v1.manifest.json": _json_bytes(
            manifest.model_dump(mode="json", by_alias=True)
        ),
        repository_root
        / "docs/plans/game-observatory/"
        / "[2026-07-15]AI-PLAYER-LIFELONG-EXPLORATION-v1/"
        / "omni-game-player-help.snapshot.txt": render_facility_help(contract).encode("utf-8"),
    }
    mismatches = [path for path, content in expected.items() if not path.is_file() or path.read_bytes() != content]
    if mismatches:
        joined = "\n".join(str(path) for path in mismatches)
        raise RuntimeError(f"external-agent contract artifacts are stale:\n{joined}")
    return tuple(expected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = (
        check_external_agent_contracts(args.repository_root)
        if args.check
        else export_external_agent_contracts(args.repository_root)
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
