"""Unified, contract-backed CLI for the local Game Observatory AI player."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import numpy as np
from PIL import Image

from omnicompany.packages.domains.game_observatory.evidence import (
    frame_layout_hash_similarities,
    perceptual_frame_distance,
    regional_perceptual_frame_distance,
    regional_structural_frame_distance,
    structural_frame_distance,
)
from omnicompany.packages.domains.game_observatory.ai_player.account_metric_observation import (
    AccountMetricDefinitionV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.account_metric_runtime import (
    extract_and_persist_screenshot_metric,
    persist_authoritative_metric,
)
from omnicompany.packages.domains.game_observatory.ai_player.console_projection import (
    build_ai_player_console_projection,
    build_path_reuse_health_projection,
    project_semantic_state,
    resolve_current_semantic_state,
)
from omnicompany.packages.domains.game_observatory.ai_player.acceptance_executor import (
    AcceptanceExecutionFailed,
    execute_acceptance_request,
)
from omnicompany.packages.domains.game_observatory.ai_player.external_agent_continuity import (
    ExternalAgentContinuousSessionV1,
    build_afkj_external_agent_manifest,
    build_player_facility_contract,
    check_external_agent_contracts,
)
from omnicompany.packages.domains.game_observatory.ai_player.external_agent_benchmark import (
    AFKJExternalAgentBenchmarkRunner,
    compare_b0_results,
    compare_b1_results,
    compare_b3_results,
    load_b0_result,
    load_b1_result,
    load_b3_result,
)
from omnicompany.packages.domains.game_observatory.ai_player.external_agent_runtime import (
    ContinuousExternalAgentRunner,
    EXTERNAL_AGENT_INVOCATION_ID_ENV,
    EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV,
    EXTERNAL_AGENT_SESSION_ID_ENV,
    ExternalAgentInvocationV1,
    ExternalAgentSessionLedger,
)
from omnicompany.packages.domains.game_observatory.ai_player.external_agent_timeout_cleanup import (
    ExternalAgentTimeoutResourceCleanup,
)
from omnicompany.packages.domains.game_observatory.ai_player.external_action_quality import (
    count_confirmed_external_action_effects,
    persist_external_action_quality_samples,
    verified_external_usage_increment,
)
from omnicompany.packages.domains.game_observatory.ai_player.explore_dispatch import (
    ExploreDispatchError,
    ExploreDispatchLedger,
    dispatch_drive,
)
from omnicompany.packages.domains.game_observatory.ai_player.runtime_version_fence import (
    detect_runtime_facility_contract_change,
)
from omnicompany.packages.domains.game_observatory.ai_player.account_policy import (
    AccountActionIntentV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.contracts import (
    AccountActionPolicyV1,
    EnvironmentScopeV1,
    EvidenceReferenceV1,
    FrontierTaskV1,
    GuideKnowledgeV1,
    MemoryRecordV1,
    NavigationFrameV1,
)
from omnicompany.packages.domains.game_observatory.ai_player.environment_promotion_seed import (
    ingest_environment_promotion_seed,
)
from omnicompany.packages.domains.game_observatory.ai_player.deferred_sedimentation import (
    settle_deferred_skill_runs,
)
from omnicompany.packages.domains.game_observatory.ai_player.crystallizer import (
    SkillCrystallizationRequestV1,
    SkillCrystallizer,
)
from omnicompany.packages.domains.game_observatory.ai_player.guarded_skill_step_adapter import (
    GuardedSkillActionReceiptV1,
    GuardedSkillObservationReceiptV1,
    GuardedSkillStepAdapter,
)
from omnicompany.packages.domains.game_observatory.ai_player.guide_research import (
    load_guide_seed,
)
from omnicompany.packages.domains.game_observatory.ai_player.guide_refresh import (
    triggered_refresh_focus_hint,
)
from omnicompany.packages.domains.game_observatory.ai_player.guide_refresh_cli import (
    guide_refresh_group,
)
from omnicompany.packages.domains.game_observatory.ai_player.live_step import (
    LiveStepRouteRuntime,
    LiveStepExpectationV1,
    LiveStepRequestV1,
    run_live_step,
)
from omnicompany.packages.domains.game_observatory.ai_player.live_facility import (
    LiveInstructionStore,
    compose_round_instruction,
)
from omnicompany.packages.domains.game_observatory.ai_player.live_evidence_state_ingest import (
    auto_ingest_terminal_evidence_runs,
)
from omnicompany.packages.domains.game_observatory.ai_player.invocation_state_ingest import (
    ingest_external_invocation_state_evidence,
)
from omnicompany.packages.domains.game_observatory.ai_player.known_route_program import (
    KnownRouteProgram,
    _exact_goal_alias_key,
)
from omnicompany.packages.domains.game_observatory.ai_player.operation_memory import (
    OperationMemory,
)
from omnicompany.packages.domains.game_observatory.ai_player.gameplay_candidate_discovery import (
    discover_gameplay_candidates,
)
from omnicompany.packages.domains.game_observatory.ai_player.session_game_player_runtime import (
    SessionGamePlayerRuntimeError,
    forward_navigate_to_session_runtime,
)
from omnicompany.packages.domains.game_observatory.ai_player.template_locator import (
    PixelBounds,
    TemplateLocationError,
    locate_dynamic_template,
)
from omnicompany.packages.domains.game_observatory.ai_player.session_control import (
    AIPlayerSessionBudgetCorrectionCommand,
    AIPlayerSessionError,
    AIPlayerSessionCheckpointCommand,
    AIPlayerSessionCommand,
    AIPlayerSessionControl,
    AIPlayerSessionHeartbeatCommand,
    AIPlayerSessionReconcileCommand,
    CreateAIPlayerSessionCommand,
    DEFAULT_SESSION_LEASE_TTL_SECONDS,
)
from omnicompany.packages.domains.game_observatory.ai_player.sanguo_daily_continuity import (
    DAILY_DUTIES,
    SanguoDailyContinuityError,
    SanguoDailyContinuityLedger,
)
from omnicompany.packages.domains.game_observatory.ai_player.sanguo_daily_coordinator import (
    SanguoDailyCoordinator,
    SanguoDailyDutyCandidateV1,
    SanguoDailySealCandidateV1,
)


from omnicompany.packages.domains.game_observatory.ai_player.skill_attestation import (
    skill_runtime_signer_and_trust_store,
)
from omnicompany.packages.domains.game_observatory.ai_player.skill_runtime import (
    SkillExecutionRequestV1,
    SkillRuntime,
)
from omnicompany.packages.domains.game_observatory.ai_player.skill_candidate_discovery import (
    crystallize_repeated_atomic_skill_candidates,
)
from omnicompany.packages.domains.game_observatory.ai_player.skills import (
    SkillLifecycle,
    SkillLifecycleError,
    skill_is_applicable,
)
from omnicompany.packages.domains.game_observatory.ai_player.skill_validation import (
    derive_skill_validation,
)
from omnicompany.packages.domains.game_observatory.ai_player.semantic_surface_profiles import (
    resolve_reusable_semantic_surface_profile_pair,
)
from omnicompany.packages.domains.game_observatory.models import utc_now
from omnicompany.packages.domains.game_observatory.models import (
    ArtifactRef,
    EvidenceDynamicSceneProfile,
    EvidenceRun,
    EvidenceStability,
    EvidenceStep,
    NormalizedAction,
    SourcePixelPoint,
    SourcePixelRect,
    SourceSnapshot,
)
from omnicompany.packages.domains.game_observatory.ai_player.state_graph import (
    SemanticStateGraph,
)
from omnicompany.packages.domains.game_observatory.ai_player.state_adjudication import (
    StateAdjudicatorTrustStore,
    apply_state_adjudication_seed,
    export_state_review_packet,
    sign_state_adjudication_seed,
)
from omnicompany.packages.domains.game_observatory.ai_player.store import AIPlayerStore
from omnicompany.packages.domains.game_observatory.ai_player.surface_anchor_action import (
    SurfaceAnchorActionError,
    build_task_relevant_consensus_locator_hints,
    build_task_relevant_surface_anchor_actions,
    guard_locator_element_for_surface_anchor,
    resolve_surface_anchor_action_plan,
)
from omnicompany.packages.domains.game_observatory.ai_player.state_recognition import (
    SemanticStateRecognizer,
)
from omnicompany.packages.domains.game_observatory.ai_player.visual_locator_service import (
    CanonicalVisualLocatorService,
)
from omnicompany.packages.domains.game_observatory.runtime import GameObservatory
from omnicompany.packages.domains.game_observatory.store import default_observatory_root


_SANGUO_ACCOUNT_METRIC_DEFINITION_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "domains"
    / "game_observatory"
    / "ai_player"
    / "definitions"
    / "sanguo_account_metrics"
)


def _direct_account_metric_focus(game_id: str) -> dict[str, str]:
    if game_id not in {
        "sanguo-mouding-tianxia",
        "sanguo-mou-ding-tian-xia",
        "nslg",
    }:
        return {}
    names: list[str] = []
    for path in sorted(_SANGUO_ACCOUNT_METRIC_DEFINITION_ROOT.glob("*.json")):
        AccountMetricDefinitionV1.model_validate_json(path.read_bytes())
        names.append(path.stem)
    return {
        "root": str(_SANGUO_ACCOUNT_METRIC_DEFINITION_ROOT),
        "files": ",".join(names),
        "contract": "integer;ocr>=0.95;no 万/亿;canonical Before/After only",
    }


_AGENT_EFFECT_SCOPE = "visual_state_change_only"
_POINTER_ACTION_TYPES = {
    "tap",
    "swipe",
    "pinch",
    "two_finger_swipe",
    "mouse_move",
    "mouse_button",
}
_EXTERNAL_LOCATOR_TIMEOUT_FLOOR_SECONDS = 90.0
_LOCATOR_AGENT_BRIEF_MAX_BYTES = 3072
_AGENT_RECEIPT_RETRY_DELAYS_SECONDS = (0.05, 0.10, 0.20, 0.40, 0.80, 1.60, 3.20)
_SKILL_INVALIDATION_AGENT_BRIEF_MAX_BYTES = 2048


@dataclass(frozen=True)
class _PlayerCLIContext:
    root: Path | None
    as_json: bool
    agent_brief: bool = False
    runtime: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def facility(self) -> GameObservatory:
        facility = self.runtime.get("facility")
        if not isinstance(facility, GameObservatory):
            facility = GameObservatory(self.root)
            self.runtime["facility"] = facility
        return facility

    def player(self) -> AIPlayerStore:
        player = self.runtime.get("player")
        if not isinstance(player, AIPlayerStore):
            player = AIPlayerStore(self.facility().store)
            self.runtime["player"] = player
        return player


def _model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def _emit(context: _PlayerCLIContext, payload: dict[str, Any], *, summary: str) -> None:
    body = {"ok": True, **payload}
    emission_sink = context.runtime.get("emission_sink")
    if isinstance(emission_sink, list):
        emission_sink.append(body)
    if context.runtime.get("suppress_output"):
        return
    if context.as_json:
        click.echo(json.dumps(body, ensure_ascii=False, indent=2, default=_model))
        return
    click.echo(summary)
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            click.echo(f"  {key}: {value}")
        elif isinstance(value, list):
            click.echo(f"  {key}: {len(value)} 项")
        elif isinstance(value, dict):
            click.echo(f"  {key}: {len(value)} 个字段")


def _preview_pointer_contract(
    mapping: dict[str, Any],
    *,
    evidence_step_id: str,
) -> dict[str, Any]:
    """Declare that attached-preview pixels must use the existing tap-preview path."""

    return {
        "image_coordinate_space": "agent_preview_pixels",
        "preview_size": [
            int(mapping.get("preview_width") or 0),
            int(mapping.get("preview_height") or 0),
        ],
        "source_size": [
            int(mapping.get("source_width") or 0),
            int(mapping.get("source_height") or 0),
        ],
        "execute_pointer_with": "act tap-preview",
        "source_step_id": evidence_step_id,
        "forbid_preview_pixels_with": "act tap",
        "conversion": "CLI_auto",
    }


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _require_environment(player: AIPlayerStore, environment_id: str) -> Any:
    environment = player.get_environment(environment_id)
    if environment is None:
        raise click.ClickException(f"没有这个 AI 玩家环境: {environment_id}")
    return environment


def _environment_identity_hash(
    *,
    game_id: str,
    game_id_aliases: tuple[str, ...],
    build_scope_id: str,
    build_scope_id_aliases: tuple[str, ...],
    account_scope_id: str,
    channel: str,
    device_scope_id: str,
    device_scope_id_aliases: tuple[str, ...],
    server_scope_id: str | None,
    world_scope_id: str | None,
    locale: str,
    viewport_width: int,
    viewport_height: int,
) -> str:
    payload = {
        "game_id": game_id,
        "game_id_aliases": sorted(game_id_aliases),
        "build_scope_id": build_scope_id,
        "build_scope_id_aliases": sorted(build_scope_id_aliases),
        "account_scope_id": account_scope_id,
        "channel": channel,
        "device_scope_id": device_scope_id,
        "device_scope_id_aliases": sorted(device_scope_id_aliases),
        "server_scope_id": server_scope_id,
        "world_scope_id": world_scope_id,
        "locale": locale,
        "viewport_width": viewport_width,
        "viewport_height": viewport_height,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _png_dimensions(artifact: ArtifactRef) -> tuple[int, int]:
    path = Path(artifact.path)
    if not path.is_file():
        raise click.ClickException(f"环境登记截图不存在: {artifact.id}")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise click.ClickException(f"环境登记截图 hash 不一致: {artifact.id}")
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise click.ClickException("环境登记要求 ADB 返回有效 PNG 截图。")
    return struct.unpack(">II", raw[16:24])


def _agent_preview(
    facility: GameObservatory,
    *,
    source_frame: ArtifactRef,
    environment_id: str,
    binding_suffix: str,
    source_width: int,
    source_height: int,
) -> tuple[ArtifactRef, dict[str, float | int]]:
    """Build one content-addressed small visual for native CLI agents."""

    preview_dir = facility.store.artifact_root / "agent_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / f"{source_frame.sha256}.jpg"
    if not preview_path.is_file():
        with Image.open(source_frame.path) as image:
            preview = image.convert("RGB")
            # Dense landscape game UIs lose small labels and adjacent hit targets at
            # 540 px wide.  A 960 px ceiling keeps portrait captures unchanged while
            # giving landscape agents enough detail to choose a target once instead
            # of spending extra turns and image tokens recovering from misclicks.
            preview.thumbnail((960, 960), Image.Resampling.LANCZOS)
            preview.save(preview_path, format="JPEG", quality=78, optimize=True)
    preview_raw = preview_path.read_bytes()
    with Image.open(preview_path) as preview_image:
        preview_width, preview_height = preview_image.size
    artifact = ArtifactRef(
        id=f"art.agent-preview.{binding_suffix}",
        kind="screenshot",
        path=str(preview_path),
        sha256=hashlib.sha256(preview_raw).hexdigest(),
        captured_at=source_frame.captured_at,
        run_id=source_frame.run_id,
        media_type="image/jpeg",
        metadata={
            "environment_id": environment_id,
            "source_artifact_id": source_frame.id,
            "source_sha256": source_frame.sha256,
            "role": "agent_preview",
            "source_width": source_width,
            "source_height": source_height,
            "preview_width": preview_width,
            "preview_height": preview_height,
        },
    )
    facility.store.save_artifact(artifact)
    return artifact, {
        "source_width": source_width,
        "source_height": source_height,
        "preview_width": preview_width,
        "preview_height": preview_height,
        "source_pixel_scale_x": source_width / preview_width,
        "source_pixel_scale_y": source_height / preview_height,
    }


def _atomic_agent_receipt(
    context: _PlayerCLIContext,
    *,
    category: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
            default=_model,
        )
        + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    directory = context.facility().store.root / "agent_receipts" / category / digest[:2]
    path = directory / f"{digest}.json"
    directory.mkdir(parents=True, exist_ok=True)
    last_error: PermissionError | None = None
    for attempt in range(len(_AGENT_RECEIPT_RETRY_DELAYS_SECONDS) + 1):
        try:
            if path.is_file():
                existing = path.read_bytes()
                if hashlib.sha256(existing).hexdigest() == digest:
                    last_error = None
                    break
                # A concurrent exclusive writer may have created the final name
                # but not finished its single payload write yet.  Give it the same
                # bounded retry window before diagnosing durable corruption.
                if attempt == len(_AGENT_RECEIPT_RETRY_DELAYS_SECONDS):
                    raise click.ClickException(f"Agent receipt hash mismatch: {path}")
            else:
                # The final pathname is content-addressed and unique.  Exclusive
                # creation avoids the Windows temp-file rename that antivirus can
                # lock after observing the newly written temporary file.
                with path.open("xb") as stream:
                    stream.write(raw)
                last_error = None
                break
        except FileExistsError:
            # Another nested CLI won the exclusive create race.  The next pass
            # verifies that its bytes are identical.
            pass
        except PermissionError as exc:
            last_error = exc
        if attempt < len(_AGENT_RECEIPT_RETRY_DELAYS_SECONDS):
            time.sleep(_AGENT_RECEIPT_RETRY_DELAYS_SECONDS[attempt])
    if last_error is not None:
        raise last_error
    return {"path": str(path), "sha256": digest, "size_bytes": len(raw)}


def _emit_compact_json(context: _PlayerCLIContext, payload: dict[str, Any]) -> None:
    sink = context.runtime.get("emission_sink")
    if isinstance(sink, list):
        sink.append(payload)
    if not context.runtime.get("suppress_output"):
        click.echo(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                default=_model,
            )
        )


def _one_line_agent_summary(value: Any, *, limit: int = 120) -> str:
    if isinstance(value, dict):
        for key in ("summary", "reason", "description", "title", "outcome", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                value = candidate
                break
        else:
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif not isinstance(value, str):
        value = str(value)
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


_WAIT_SECONDS_PATTERN = re.compile(r"(?m)^\s*WAIT_SECONDS=(\d{1,6})\s*$")
_WAIT_SCOPE_PATTERN = re.compile(r"(?mi)^\s*WAIT_SCOPE=foreground_blocking\s*$")
_A2_TURN_STOP_REASON_PATTERN = re.compile(r"(?mi)^\s*A2_TURN_STOP_REASON=([a-z_]+)\s*$")
_A2_ACTIONS_EXECUTED_PATTERN = re.compile(r"(?mi)^\s*A2_ACTIONS_EXECUTED=(\d{1,2})\s*$")
_A2_MICRO_STAGE_SUMMARY_PATTERN = re.compile(
    r"(?mi)^\s*A2_MICRO_STAGE_SUMMARY=([^\r\n]{1,240})\s*$"
)
_A2_TURN_STOP_REASONS = frozenset(
    {
        "micro_stage_complete",
        "action_cap",
        "unfamiliar_or_ambiguous",
        "expectation_failed",
        "high_impact_irreversible",
        "task_exit",
        "facility_error",
        "two_no_gain",
        "budget_insufficient",
        "foreground_blocking",
    }
)

_EXTERNAL_AGENT_HOST_CONTROL_BOUNDARY = (
    "角色边界：你是游戏执行代理，外层主控负责 Session、drive、provider、lease 和进程"
    "生命周期。禁止对当前 Session 调用 session start/resume/rollover/stop/checkpoint、"
    "explore drive/run/dispatch/dispatch-status/status/interrupt，也禁止检查或终止承载自己的 PID/lease。当前调用"
    "运行时看到 external status=starting、invocation_count=0、semantic_action_count=0 或"
    "自身 invocation lease 都是正常瞬时状态，不能据此暂停、safe-stop、记录设施阻断或"
    "递归启动另一个 Agent。你只负责 observe、act、state、task、memory、guide 和 skill"
    "等游戏工作；动作命令可用时直接玩，生命周期异常交回外层主控处理。"
)


def _declared_wait_seconds(value: str) -> int | None:
    if _WAIT_SCOPE_PATTERN.search(value) is None:
        return None
    match = _WAIT_SECONDS_PATTERN.search(value)
    if match is None:
        return None
    seconds = int(match.group(1))
    return seconds if seconds > 0 else None


# A short foreground wait is part of one continuous play stage.  Longer waits
# return control to the detached supervisor so the main process never blocks
# silently for minutes.
_MAX_IN_PROCESS_FOREGROUND_WAIT_SECONDS = 60


def _parse_a2_turn_report(
    value: str,
    *,
    actual_action_count: int,
) -> dict[str, Any]:
    """Parse and audit the provider's compact micro-stage handoff."""

    reason_matches = _A2_TURN_STOP_REASON_PATTERN.findall(value)
    action_matches = _A2_ACTIONS_EXECUTED_PATTERN.findall(value)
    summary_matches = _A2_MICRO_STAGE_SUMMARY_PATTERN.findall(value)
    reason = reason_matches[-1].lower() if reason_matches else None
    declared_action_count = int(action_matches[-1]) if action_matches else None
    summary = summary_matches[-1].strip() if summary_matches else None
    missing_fields = [
        field
        for field, present in (
            ("stop_reason", reason is not None),
            ("actions_executed", declared_action_count is not None),
            ("micro_stage_summary", bool(summary)),
        )
        if not present
    ]
    issues: list[str] = []
    if any(len(matches) > 1 for matches in (reason_matches, action_matches, summary_matches)):
        issues.append("duplicate_report_fields")
    if reason is not None and reason not in _A2_TURN_STOP_REASONS:
        issues.append("unknown_stop_reason")
    if declared_action_count is not None:
        if declared_action_count > 6:
            issues.append("declared_action_count_above_cap")
        if declared_action_count != actual_action_count:
            issues.append("declared_action_count_mismatch")
    if reason == "action_cap" and declared_action_count != 6:
        issues.append("action_cap_requires_six_actions")
    if reason == "micro_stage_complete" and (
        declared_action_count is None or not 1 <= declared_action_count <= 6
    ):
        issues.append("completed_micro_stage_requires_one_to_six_actions")
    status = "missing" if missing_fields else "invalid" if issues else "complete"
    return {
        "schema": "game-observatory.ai-player.a2-turn-report.v1",
        "status": status,
        "stop_reason": reason,
        "declared_action_count": declared_action_count,
        "actual_action_count": actual_action_count,
        "micro_stage_summary": summary,
        "missing_fields": missing_fields,
        "issues": issues,
    }


def _provider_usage_efficiency(
    usage: dict[str, int] | None,
    *,
    action_count: int,
) -> dict[str, Any]:
    """Project exact invocation usage without pretending to know per-action attribution."""

    if usage is None:
        return {
            "measurement_status": "unavailable",
            "action_count": action_count,
        }
    input_tokens = int(usage.get("input_tokens", 0))
    cached_input_tokens = int(usage.get("cached_input_tokens", 0))
    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
    return {
        "measurement_status": "measured_invocation",
        "action_count": action_count,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": uncached_input_tokens,
        "output_tokens": int(usage.get("output_tokens", 0)),
        "reasoning_tokens": int(usage.get("reasoning_tokens", 0)),
        "cache_ratio": (cached_input_tokens / input_tokens if input_tokens else None),
        "input_tokens_per_action": (input_tokens / action_count if action_count else None),
        "uncached_input_tokens_per_action": (
            uncached_input_tokens / action_count if action_count else None
        ),
    }


def _external_ledger(context: _PlayerCLIContext) -> ExternalAgentSessionLedger:
    return ExternalAgentSessionLedger(context.facility().store.root)


def _external_agent_start_prompt(
    context: _PlayerCLIContext,
    *,
    session_id: str,
    environment_id: str,
    objective: str,
    task: str,
) -> str:
    contract = build_player_facility_contract()
    contract_receipt = _atomic_agent_receipt(
        context,
        category="facility-contracts",
        payload={
            "ok": True,
            "contract": contract,
        },
    )
    player = context.player()
    canonical = AIPlayerSessionControl(player).get_session(environment_id, session_id)
    environment = player.get_environment(environment_id)
    current_state, current_state_basis = resolve_current_semantic_state(
        player,
        environment_id=environment_id,
    )
    active_tasks = []
    for task_id in canonical.active_task_ids if canonical is not None else []:
        active_task = player.get_task(environment_id, task_id)
        active_tasks.append(
            {
                "id": task_id,
                "title": (
                    _one_line_agent_summary(active_task.title, limit=120)
                    if active_task is not None
                    else None
                ),
                "status": active_task.status if active_task is not None else "missing",
            }
        )
    latest_evidence = (
        canonical.last_evidence_refs[-1] if canonical and canonical.last_evidence_refs else None
    )
    contract_brief = {
        "schema": "game-observatory.ai-player.facility-contract-brief.v1",
        "facility_contract_sha256": contract.facility_contract_sha256,
        "public_root": contract.public_root,
        "command_names": [command.id for command in contract.commands],
        "help": "omni game player --help；具体命令用 omni game player <命令> --help",
        "full_contract_ref": contract_receipt["path"],
        "full_contract_sha256": contract_receipt["sha256"],
    }
    focus = {
        "schema": "game-observatory.ai-player.session-focus-brief.v1",
        "environment": (
            {
                "id": environment.id,
                "game_id": environment.game_id,
                "build_scope_id": environment.build_scope_id,
                "account_scope_id": environment.account_scope_id,
                "device_scope_id": environment.device_scope_id,
                "channel": environment.channel,
            }
            if environment is not None
            else {"id": environment_id}
        ),
        "session": (
            {
                "id": canonical.id,
                "state": canonical.state,
                "active_tasks": active_tasks,
                "last_evidence": (
                    {
                        "artifact_ids": latest_evidence.artifact_ids[-2:],
                        "evidence_run_ids": latest_evidence.evidence_run_ids[-2:],
                        "evidence_step_ids": latest_evidence.evidence_step_ids[-2:],
                    }
                    if latest_evidence is not None
                    else None
                ),
                "budget": {
                    "remaining_actions": canonical.remaining_action_budget,
                    "remaining_tokens": canonical.remaining_token_budget,
                    "remaining_seconds": canonical.remaining_time_seconds,
                },
            }
            if canonical is not None
            else {"id": session_id, "state": "missing"}
        ),
        "current_state": (
            {
                "id": current_state.id,
                "title": current_state.title,
                "status": current_state.status,
                "basis": current_state_basis,
            }
            if current_state is not None
            else None
        ),
    }
    return (
        "你是持续运行的 AI 玩家。首轮只加载设施合同摘要；完整合同保存在摘要给出的内容寻址"
        "回执中，仅在具体命令无法由 help 确认时按需读取。后续轮次通过 resume 延续理解，"
        "禁止清除或重建原生会话历史。设备和账号操作只调用 omni game player，遵守其中的目标、"
        "遮罩、来源、账号和证据守卫。一个 EvidenceStep 是动作证据边界，不是 Agent 生命周期"
        "边界。运行前写清目标、当前状态、预期变化、动作预算和停止条件；运行后核对实际变化、"
        "证据、信息增量、副作用和下一任务。目标界面已经明确时，第一条命令直接调用 "
        "navigate <目标>，由程序层查询状态图、技能图并执行最低成本已知路径。普通游玩轮次"
        "禁止读取完整 state map 或无过滤 skill list；navigate 在设备动作前返回无已知路径后，"
        "才观察当前界面并只探索缺失片段。新界面允许步进探索。"
        "固定层回放后若终态界面与目标语义明确不符，直接使用回执的 after_preview 与 "
        "terminal_evidence_step_id；只有回执缺图时才 observe capture --focus。使用紧凑回执 "
        "executed_skills 中的精确版本号调用 skill invalidate，禁止为取得"
        "版本号读取完整回执；参数为 --source-step <终态步骤> "
        "--expected-target <原目标> --observed-screen <实际界面>；不得停用未实际回放的技能。"
        "只有控件在前置条件满足时进入了无关业务界面，才属于语义错标并可停用。资源不足、"
        "部队或任务占用、冷却、解锁条件、次数限制以及确认或警告弹窗都属于运行时前置失败；"
        "保留失败 SkillRun 和终态证据，禁止因此停用技能。"
        "对白推进或局部文字替换使用 min_visual_distance=0.01；场景或布局迁移使用 0.03，"
        "并以实际文本、角色和界面变化复核阈值判断。"
        "全部正常游戏内行为均已授权自主执行，包括消耗虚拟黄金、铜钱、体力、道具和其他"
        "游戏内资源；按优秀玩家的长期收益自行决策。禁止真实货币支付和提交外部个人身份"
        "资料。账号发言仅在必要时按作者口径进行。"
        "本轮应在同一个 Session 内完成一段有意义的连续工作，避免逐动作退出、重复读取完整"
        "帮助和无信息增量空转。"
        f"本轮唯一可写 canonical Session ID 是 {session_id}；所有带 --session 的命令必须"
        "使用该值，上一代 Session ID 只属于历史证据，禁止继续写入。"
        f"{_EXTERNAL_AGENT_HOST_CONTROL_BOUNDARY}\n\n"
        f"阶段目标：{objective}\n\n"
        f"设施合同：\n{json.dumps(contract_brief, ensure_ascii=False, separators=(',', ':'))}"
        f"\n\n焦点上下文：\n{json.dumps(focus, ensure_ascii=False)}"
        f"\n\n本轮任务：\n{task.strip()}"
    )


def _ensure_canonical_session_running(
    control: AIPlayerSessionControl,
    *,
    session_id: str,
    environment_id: str,
    holder: str,
    lease_ttl_seconds: int = DEFAULT_SESSION_LEASE_TTL_SECONDS,
) -> Any:
    current = control.get_session(environment_id, session_id)
    if current is None:
        raise click.ClickException(f"canonical AI 玩家 Session 不存在: {session_id}")
    if current.state == "running":
        for attempt in range(2):
            try:
                control.assert_session_lease_active(environment_id, session_id)
                return control.heartbeat(
                    session_id,
                    AIPlayerSessionHeartbeatCommand(
                        command_id=(
                            f"{session_id}.external-turn-start.{current.version}."
                            f"{uuid.uuid4().hex[:12]}"
                        ),
                        environment_id=environment_id,
                        expected_version=current.version,
                        actor="ai-player-external-agent",
                        reason="外部 Agent 新一轮调用开始，刷新 worker 租约。",
                        lease_id=current.lease_id,
                        lease_holder=current.lease_holder,
                        lease_ttl_seconds=lease_ttl_seconds,
                    ),
                )
            except AIPlayerSessionError as exc:
                if exc.code == "version_conflict" and attempt == 0:
                    refreshed = control.get_session(environment_id, session_id)
                    if refreshed is not None and refreshed.state == "running":
                        current = refreshed
                        continue
                if exc.code == "budget_exhausted":
                    raise click.ClickException(exc.message) from None
                break
            except Exception:  # noqa: BLE001 - stale reconciliation owns repair
                break
        control.reconcile_stale_sessions(
            AIPlayerSessionReconcileCommand(
                command_id=f"{session_id}.external-resume.reconcile.{current.version}",
                environment_id=environment_id,
                actor="ai-player-external-agent",
                reason="恢复外部 Agent 前收口陈旧 worker lease。",
            )
        )
        current = control.get_session(environment_id, session_id)
    if current is None or current.state != "paused":
        raise click.ClickException(
            f"canonical Session 处于 {getattr(current, 'state', 'missing')}，无法恢复"
        )
    return control.resume(
        session_id,
        AIPlayerSessionCommand(
            command_id=f"{session_id}.external-resume.{current.version}",
            environment_id=environment_id,
            expected_version=current.version,
            actor="ai-player-external-agent",
            reason="续接同一外部 Agent Session。",
            lease_holder=holder,
            lease_ttl_seconds=lease_ttl_seconds,
        ),
    )


def _recover_stale_external_running_session(
    context: _PlayerCLIContext,
    control: AIPlayerSessionControl,
    *,
    session_id: str,
    environment_id: str,
) -> Any:
    """Reacquire an expired lease without replacing the native provider Session."""

    current = control.get_session(environment_id, session_id)
    if current is None:
        raise click.ClickException(f"Session 不存在: {session_id}")
    if current.state != "running":
        return current
    try:
        control.assert_session_lease_active(environment_id, session_id)
        return current
    except AIPlayerSessionError:
        external = _external_ledger(context).get_session(session_id)
        if external is None or external.environment_id != environment_id:
            raise click.ClickException(
                "Session 的 worker 租约已过期，且没有可核验的持续外部 Session 可以恢复。"
            ) from None
        return _ensure_canonical_session_running(
            control,
            session_id=session_id,
            environment_id=environment_id,
            holder=f"external-agent:{external.provider}:{external.id}",
        )


_SAFE_ADDITIVE_EXTERNAL_CONTRACT_UPGRADES = {
    *{
        (previous, "4de938323a6bed43b38982c0dc4f8069e89e9071e9e5a1826dbd19a9b66f391d")
        for previous in (
            "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
            "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
            "a9a61bf2682fa06c0a4b3dbdaa227d69d7d5122a45030bee6b71e6cf7e07fdaf",
            "5194af215ea9f4cc41361d104bad6ea1b446d9d8101cb55eb6f469e6303ad138",
            "7aad10bdea9afb1dad198eb0fa044cd77ae152900e99023c789064b7f802a34d",
            "ed78f9f64087ee15f9e23e93e8b0fc72ebd973b8fa0867bcb3d2409990111ffa",
            "0dc09faeda4b8093bf2ae2683f803e4653b48589cf28b2f3d4b38445989ea216",
            "7c2bb4b702fa8b4aa808bdd0d71b26d497cf343d4762b6cbaa1ae735b4a001f1",
        )
    },
    *{
        (previous, "7c2bb4b702fa8b4aa808bdd0d71b26d497cf343d4762b6cbaa1ae735b4a001f1")
        for previous in (
            "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
            "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
            "a9a61bf2682fa06c0a4b3dbdaa227d69d7d5122a45030bee6b71e6cf7e07fdaf",
            "5194af215ea9f4cc41361d104bad6ea1b446d9d8101cb55eb6f469e6303ad138",
            "7aad10bdea9afb1dad198eb0fa044cd77ae152900e99023c789064b7f802a34d",
            "ed78f9f64087ee15f9e23e93e8b0fc72ebd973b8fa0867bcb3d2409990111ffa",
            "0dc09faeda4b8093bf2ae2683f803e4653b48589cf28b2f3d4b38445989ea216",
        )
    },
    *{
        (previous, "0dc09faeda4b8093bf2ae2683f803e4653b48589cf28b2f3d4b38445989ea216")
        for previous in (
            "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
            "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
            "a9a61bf2682fa06c0a4b3dbdaa227d69d7d5122a45030bee6b71e6cf7e07fdaf",
            "5194af215ea9f4cc41361d104bad6ea1b446d9d8101cb55eb6f469e6303ad138",
            "7aad10bdea9afb1dad198eb0fa044cd77ae152900e99023c789064b7f802a34d",
            "ed78f9f64087ee15f9e23e93e8b0fc72ebd973b8fa0867bcb3d2409990111ffa",
        )
    },
    (
        "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
        "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
    ),
    (
        "a9a61bf2682fa06c0a4b3dbdaa227d69d7d5122a45030bee6b71e6cf7e07fdaf",
        "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
    ),
    (
        "5194af215ea9f4cc41361d104bad6ea1b446d9d8101cb55eb6f469e6303ad138",
        "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
    ),
    (
        "7aad10bdea9afb1dad198eb0fa044cd77ae152900e99023c789064b7f802a34d",
        "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
    ),
    (
        "ed78f9f64087ee15f9e23e93e8b0fc72ebd973b8fa0867bcb3d2409990111ffa",
        "67b42afd5934e052136006b80c25361cf6a260fe886745947f4d0524f6420794",
    ),
    (
        "a9a61bf2682fa06c0a4b3dbdaa227d69d7d5122a45030bee6b71e6cf7e07fdaf",
        "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
    ),
    (
        "5194af215ea9f4cc41361d104bad6ea1b446d9d8101cb55eb6f469e6303ad138",
        "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
    ),
    (
        "7aad10bdea9afb1dad198eb0fa044cd77ae152900e99023c789064b7f802a34d",
        "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
    ),
    (
        "ed78f9f64087ee15f9e23e93e8b0fc72ebd973b8fa0867bcb3d2409990111ffa",
        "0b494f46044ac57a2e97d041d0ce7fe8bd3eb53706b1b3b91730949bf4a498ff",
    ),
    (
        "a9a61bf2682fa06c0a4b3dbdaa227d69d7d5122a45030bee6b71e6cf7e07fdaf",
        "7aad10bdea9afb1dad198eb0fa044cd77ae152900e99023c789064b7f802a34d",
    ),
    (
        "5194af215ea9f4cc41361d104bad6ea1b446d9d8101cb55eb6f469e6303ad138",
        "7aad10bdea9afb1dad198eb0fa044cd77ae152900e99023c789064b7f802a34d",
    ),
}


def _require_external_contract_current(
    context: _PlayerCLIContext,
    external: ExternalAgentContinuousSessionV1,
) -> ExternalAgentContinuousSessionV1:
    current_contract = build_player_facility_contract()
    current_hash = current_contract.facility_contract_sha256
    if external.facility_contract_sha256 == current_hash:
        return external
    ledger = _external_ledger(context)
    if (
        external.status in {"active", "suspended"}
        and (external.facility_contract_sha256, current_hash)
        in _SAFE_ADDITIVE_EXTERNAL_CONTRACT_UPGRADES
    ):
        commands = {item.id: item for item in current_contract.commands}
        locate = commands.get("observe.locate")
        tap_element = commands.get("act.tap-element")
        tap_anchor = commands.get("act.tap-anchor")
        rollover = commands.get("session.rollover")
        dispatch = commands.get("explore.dispatch")
        dispatch_status = commands.get("explore.dispatch-status")
        daily_schema = commands.get("daily.schema")
        daily_status = commands.get("daily.status")
        daily_advance = commands.get("daily.advance")
        daily_seal = commands.get("daily.seal")
        reconcile_operation_memory = commands.get(
            "skill.reconcile-operation-memory"
        )
        if (
            locate is not None
            and locate.mutation_scope == "local_state"
            and locate.guard_profile == "local_write"
            and tap_element is not None
            and tap_element.mutation_scope == "device"
            and tap_element.guard_profile == "device_preflight"
            and tap_anchor is not None
            and tap_anchor.mutation_scope == "device"
            and tap_anchor.guard_profile == "device_preflight"
            and rollover is not None
            and rollover.mutation_scope == "local_state"
            and rollover.guard_profile == "local_write"
            and dispatch is not None
            and dispatch.mutation_scope == "device"
            and dispatch.guard_profile == "device_preflight"
            and dispatch_status is not None
            and dispatch_status.mutation_scope == "read_only"
            and dispatch_status.guard_profile == "none"
            and daily_schema is not None
            and daily_schema.mutation_scope == "read_only"
            and daily_schema.guard_profile == "none"
            and daily_status is not None
            and daily_status.mutation_scope == "read_only"
            and daily_status.guard_profile == "none"
            and daily_advance is not None
            and daily_advance.mutation_scope == "local_state"
            and daily_advance.guard_profile == "local_write"
            and daily_seal is not None
            and daily_seal.mutation_scope == "local_state"
            and daily_seal.guard_profile == "local_write"
            and reconcile_operation_memory is not None
            and reconcile_operation_memory.mutation_scope == "local_state"
            and reconcile_operation_memory.guard_profile == "local_write"
        ):
            timestamp = utc_now()
            return ledger.update_session(
                external.model_copy(
                    update={
                        "version": external.version + 1,
                        "facility_contract_sha256": current_hash,
                        "last_error": None,
                        "updated_at": timestamp,
                    }
                ),
                expected_version=external.version,
            )
    if external.status != "suspended":
        timestamp = utc_now()
        external = ledger.update_session(
            ExternalAgentContinuousSessionV1.model_validate(
                {
                    **external.model_dump(mode="json", by_alias=True),
                    "version": external.version + 1,
                    "status": "suspended",
                    "last_error": (
                        "设施合同已变化；必须重新加载完整合同并按允许的重启原因建立新一代 Session。"
                    ),
                    "last_heartbeat_at": timestamp,
                    "updated_at": timestamp,
                }
            ),
            expected_version=external.version,
        )
    control = AIPlayerSessionControl(context.player())
    canonical = control.get_session(external.environment_id, external.id)
    if canonical is not None and canonical.state == "running":
        control.pause(
            canonical.id,
            AIPlayerSessionCommand(
                command_id=f"{canonical.id}.contract-change.{canonical.version}",
                environment_id=canonical.environment_id,
                expected_version=canonical.version,
                actor="ai-player-cli",
                reason=("设施合同 hash 已变化，暂停设备计划并要求重新加载完整合同。"),
            ),
        )
    raise click.ClickException(
        "设施合同已变化，当前 Session 已暂停："
        f"{external.facility_contract_sha256} -> {current_hash}。"
        "请运行 context export，并以 restart_reason=facility_contract_change 建立新一代 Session。"
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"无法读取 JSON 文件 {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise click.ClickException(f"JSON 文件必须是对象: {path}")
    return payload


def _evidence_reference_from_step(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    step_id: str,
    require_semantic_source: bool = False,
) -> tuple[Any, Any, Any, EvidenceReferenceV1]:
    facility = context.facility()
    player = AIPlayerStore(facility.store)
    environment = _require_environment(player, environment_id)
    step = facility.store.get_evidence_step(step_id)
    if step is None or step.status != "passed" or not step.ended_at:
        raise click.ClickException("来源 EvidenceStep 必须存在且已通过终态检查。")
    run = facility.store.get_evidence_run(step.evidence_run_id)
    if run is None or run.status != "passed" or not run.ended_at:
        raise click.ClickException("来源 EvidenceRun 必须存在且已通过终态检查。")
    if run.scope_id != environment.id:
        raise click.ClickException("来源 EvidenceStep 属于另一个游戏环境。")
    artifact = facility.store.get_artifact(str(step.after_frame_id))
    if artifact is None or not Path(artifact.path).is_file():
        raise click.ClickException("来源 EvidenceStep 缺少可读取的 After 截图。")
    if require_semantic_source and artifact.metadata.get("semantic_state_eligible") is not True:
        raise click.ClickException("来源 After 截图尚未被标记为可用于语义动作定位。")
    reference = EvidenceReferenceV1(
        environment_id=environment.id,
        artifact_ids=[artifact.id],
        evidence_run_ids=[run.id],
        evidence_step_ids=[step.id],
        trace_run_ids=[step.action_run_id] if step.action_run_id else [],
        note="上一动作的稳定终态，作为本次计划或动作的 canonical 来源。",
    )
    player.resolve_evidence_references([reference], environment_scope=environment)
    return environment, run, step, reference


def _resolve_environment_serial(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    serial: str | None,
) -> str:
    environment = _require_environment(context.player(), environment_id)
    if serial:
        resolved = serial.strip()
        direct_target_id = f"device://adb/{resolved}"
        accepted = {environment.device_scope_id, *environment.device_scope_id_aliases}
        if direct_target_id in accepted:
            return resolved
        facility = context.facility()
        for candidate in accepted:
            target = facility.store.get_target(candidate)
            if target is not None and str(target.metadata.get("serial") or "") == resolved:
                return resolved
        raise click.ClickException(f"显式 ADB serial 不属于环境 {environment.id}: {resolved}")
    facility = context.facility()
    facility.discover_targets(refresh=True)
    candidates = [environment.device_scope_id, *environment.device_scope_id_aliases]
    for candidate in candidates:
        target = facility.store.get_target(candidate)
        if target is not None:
            resolved = str(target.metadata.get("serial") or "").strip()
            if resolved:
                return resolved
            if target.id.startswith("device://adb/"):
                return target.id.removeprefix("device://adb/")
        if candidate.startswith("device://adb/"):
            return candidate.removeprefix("device://adb/")
    raise click.ClickException(
        "环境没有可解析的在线 ADB serial；请先运行 doctor，或显式传 --serial。"
    )


_ANDROID_PACKAGE_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+){2,}\b")


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _canonical_environment_baseline_packages(
    player: AIPlayerStore,
    environment: Any,
) -> set[str]:
    """Resolve packages only from canonical benchmark/environment mappings."""

    store_root = player.observatory_store.root.resolve()
    repository_root = _repository_root().resolve()
    benchmark_root = store_root / "benchmarks" / "ai_player"
    baseline_paths: list[Path] = []
    packages: set[str] = set()
    manifest_path = benchmark_root / "acceptance_manifest.v1.json"
    manifest = _read_json_object(manifest_path)
    if manifest and manifest.get("schema") == "ai-player-acceptance-manifest.v1":
        targets = manifest.get("targets")
        if isinstance(targets, list):
            exact_targets = [
                item
                for item in targets
                if isinstance(item, dict) and item.get("current_environment_id") == environment.id
            ]
            for target in exact_targets:
                package = str(target.get("package") or "").strip()
                if _ANDROID_PACKAGE_PATTERN.fullmatch(package):
                    packages.add(package)
                raw_path = str(target.get("environment_baseline") or "").strip()
                if raw_path:
                    candidate = Path(raw_path)
                    roots = (repository_root, store_root, manifest_path.parent)
                    resolved_candidates = (
                        [candidate.resolve()]
                        if candidate.is_absolute()
                        else [(root / candidate).resolve() for root in roots]
                    )
                    baseline_paths.extend(
                        path
                        for path in resolved_candidates
                        if path.is_file()
                        and (
                            path.is_relative_to(repository_root) or path.is_relative_to(store_root)
                        )
                    )
    if not baseline_paths:
        environment_root = benchmark_root / "environments"
        if environment_root.is_dir():
            baseline_paths.extend(sorted(environment_root.glob("*.environment.json")))

    accepted_game_ids = {environment.game_id, *environment.game_id_aliases}
    for path in dict.fromkeys(item.resolve() for item in baseline_paths):
        baseline = _read_json_object(path)
        if not baseline or baseline.get("schema") != "ai-player-environment-baseline.v1":
            continue
        game = baseline.get("game")
        if not isinstance(game, dict):
            continue
        if (
            str(game.get("game_id") or "") not in accepted_game_ids
            or str(game.get("channel") or "") != environment.channel
        ):
            continue
        package = str(game.get("package") or "").strip()
        launcher_activity = str(game.get("launcher_activity") or "").strip()
        if not _ANDROID_PACKAGE_PATTERN.fullmatch(package):
            continue
        if launcher_activity and not launcher_activity.startswith(f"{package}/"):
            continue
        packages.add(package)
    return packages


def _expected_environment_package(
    player: AIPlayerStore,
    environment_id: str,
) -> str | None:
    """Resolve a source-backed Android package binding for foreground recovery."""

    environment = _require_environment(player, environment_id)
    memories = [
        item
        for item in player.list_memories(environment_id)
        if item.status == "active" and item.subject_id == "environment-runtime-package"
    ]
    for memory in reversed(memories):
        for key in ("package_id", "package", "android_package"):
            value = str(memory.payload.get(key) or "").strip()
            if _ANDROID_PACKAGE_PATTERN.fullmatch(value):
                return value
    for reference in reversed(environment.evidence_refs):
        for candidate in _ANDROID_PACKAGE_PATTERN.findall(reference.note or ""):
            if candidate.startswith(("com.", "cn.", "net.", "org.")):
                return candidate
    benchmark_packages = _canonical_environment_baseline_packages(player, environment)
    if len(benchmark_packages) == 1:
        return next(iter(benchmark_packages))
    return None


def _sync_external_action_counts(
    ledger: ExternalAgentSessionLedger,
    external: ExternalAgentContinuousSessionV1,
    canonical: Any,
) -> ExternalAgentContinuousSessionV1:
    """Reconcile public monitoring counters from the canonical action budget ledger."""

    consumed = max(0, canonical.action_budget - canonical.remaining_action_budget)
    semantic = max(external.semantic_action_count, consumed)
    atomic = max(external.atomic_action_count, semantic)
    if semantic == external.semantic_action_count and atomic == external.atomic_action_count:
        return external
    updated = external.model_copy(
        update={
            "version": external.version + 1,
            "semantic_action_count": semantic,
            "atomic_action_count": atomic,
            "updated_at": utc_now(),
        }
    )
    ledger.update_session(updated, expected_version=external.version)
    return updated


def _state_assignment_for_evidence(
    player: AIPlayerStore,
    *,
    environment_id: str,
    evidence_refs: list[EvidenceReferenceV1],
) -> Any | None:
    step_ids = {step_id for reference in evidence_refs for step_id in reference.evidence_step_ids}
    artifact_ids = {
        artifact_id for reference in evidence_refs for artifact_id in reference.artifact_ids
    }
    return player.find_current_state_assignment_for_evidence(
        environment_id,
        evidence_step_ids=sorted(step_ids),
        artifact_ids=sorted(artifact_ids),
    )


def _ingest_and_resolve_evidence_state(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    evidence_refs: list[EvidenceReferenceV1],
) -> Any | None:
    existing = _state_assignment_for_evidence(
        context.player(),
        environment_id=environment_id,
        evidence_refs=evidence_refs,
    )
    if existing is not None:
        return existing
    run_ids = list(
        dict.fromkeys(
            run_id for reference in evidence_refs for run_id in reference.evidence_run_ids
        )
    )
    if run_ids:
        ingested = auto_ingest_terminal_evidence_runs(
            context.facility().store.root,
            environment_id=environment_id,
            evidence_run_ids=run_ids,
        )
        if ingested.result is not None and ingested.result.observation_ids:
            observation_id = ingested.result.observation_ids[-1]
            assignment = context.player().get_current_state_assignment(
                environment_id,
                observation_id,
            )
            if assignment is not None and assignment.status == "active":
                return assignment
    return _state_assignment_for_evidence(
        context.player(),
        environment_id=environment_id,
        evidence_refs=evidence_refs,
    )


def _applicable_preferred_skill_briefs(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    current_state_id: str | None,
    visual_variant_id: str | None,
    limit: int = 4,
) -> tuple[list[Any], list[dict[str, Any]]]:
    if current_state_id is None:
        return [], []
    skills = SkillLifecycle(context.player()).select_preferred(
        environment_id,
        current_state_id=current_state_id,
        visual_variant_id=visual_variant_id,
    )
    skills = skills[:limit]
    return skills, [
        {
            "version_id": skill.id,
            "skill_id": skill.skill_id,
            "title": skill.title,
            "level": skill.level,
            "safety": skill.safety_level,
            "actions": sum(step.kind == "action" for step in skill.steps),
            "terminal_state_ids": list(
                dict.fromkeys(
                    step.expected_state_id
                    for step in skill.steps
                    if step.kind == "assert" and step.expected_state_id is not None
                )
            ),
        }
        for skill in skills
    ]


def _region_fingerprint_distance(
    first: dict[str, str],
    second: dict[str, str],
) -> float | None:
    """Return the mean normalized Hamming distance over shared screen regions."""

    shared = sorted(first.keys() & second.keys())
    if len(shared) < 6:
        return None
    distances: list[float] = []
    for region_id in shared:
        try:
            first_value = int(first[region_id], 16)
            second_value = int(second[region_id], 16)
        except (TypeError, ValueError):
            return None
        bit_width = max(1, len(first[region_id]), len(second[region_id])) * 4
        distances.append((first_value ^ second_value).bit_count() / bit_width)
    return sum(distances) / len(distances)


def _state_region_fingerprint_index(
    player: Any,
    environment_id: str,
    *,
    per_state_limit: int = 4,
    state_ids: Iterable[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Load the compact state signatures once instead of decoding every prototype."""

    indexed: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    state_filter = frozenset(state_ids) if state_ids is not None else None
    compact_loader = getattr(player, "list_active_state_region_fingerprint_rows", None)
    if callable(compact_loader):
        rows = compact_loader(
            environment_id,
            state_ids=(tuple(sorted(state_filter)) if state_filter is not None else None),
        )
        for row in rows:
            fingerprints = dict(row.region_fingerprints)
            if fingerprints in indexed[row.state_id]:
                continue
            indexed[row.state_id].append(fingerprints)
            indexed[row.state_id] = indexed[row.state_id][-per_state_limit:]
        return dict(indexed)

    # Compatibility for lightweight test doubles and external store adapters.
    observations = {
        item.id: item.features.region_fingerprints
        for item in player.list_state_observations(environment_id)
        if item.features.region_fingerprints
    }
    for assignment in player.list_state_assignments(environment_id):
        if assignment.status != "active":
            continue
        if state_filter is not None and assignment.state_id not in state_filter:
            continue
        fingerprints = observations.get(assignment.observation_id)
        if fingerprints is None or fingerprints in indexed[assignment.state_id]:
            continue
        indexed[assignment.state_id].append(fingerprints)
        indexed[assignment.state_id] = indexed[assignment.state_id][-per_state_limit:]
    return dict(indexed)


def _state_region_fingerprint_distance(
    index: dict[str, list[dict[str, str]]],
    first_state_id: str,
    second_state_id: str,
) -> float | None:
    distances = [
        distance
        for first in index.get(first_state_id, [])
        for second in index.get(second_state_id, [])
        if (distance := _region_fingerprint_distance(first, second)) is not None
    ]
    return min(distances) if distances else None


_OPTIONAL_CANDIDATE_VISUAL_MATCH_LIMIT = 6
_OPTIONAL_CANDIDATE_PROTOTYPE_LIMIT = 2


def _applicable_candidate_skill_briefs(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    session_id: str,
    current_state_id: str | None,
    source_step_id: str,
    baseline_model_input_tokens: int,
    baseline_decision_latency_ms: float,
    limit: int = 3,
    skill_versions: Iterable[Any] | None = None,
    skill_runs: Iterable[Any] | None = None,
) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate virgin candidate trials from candidates that already proved usable.

    Once a candidate has one guarded successful run it belongs to the learned
    route graph.  Recommending another explicit ``skill replay`` makes the
    semantic Agent keep deciding an operation the deterministic router already
    knows.  Keep only never-run candidates in the trial list and expose warm
    candidates as goal-level ``navigate`` commands.
    """

    if current_state_id is None:
        return [], [], []
    player = context.player()
    environment = player.get_environment(environment_id)
    if environment is None:
        return [], [], []
    latest_by_skill: dict[str, Any] = {}
    candidate_skill_versions = (
        player.list_skill_versions(environment_id)
        if skill_versions is None
        else tuple(skill_versions)
    )
    candidate_skill_runs = (
        tuple(player.list_skill_runs(environment_id))
        if skill_runs is None
        else tuple(skill_runs)
    )
    for skill in candidate_skill_versions:
        previous = latest_by_skill.get(skill.skill_id)
        if previous is None or skill.version > previous.version:
            latest_by_skill[skill.skill_id] = skill
    successful_run_counts = _successful_skill_run_counts(
        player,
        environment_id,
        skill_runs=candidate_skill_runs,
    )
    existing_run_counts: dict[str, int] = defaultdict(int)
    for run in candidate_skill_runs:
        existing_run_counts[run.skill_version_id] += 1
    candidate_by_operation: dict[str, Any] = {}
    for skill in latest_by_skill.values():
        if skill.status != "candidate":
            continue
        # Candidate multi-action flows are historical proposals, not the
        # preferred execution primitive.  The fixed atomic graph can assemble
        # them with current-state guards and exposes only the atomic candidates
        # that still need one independent validation.
        if getattr(skill, "level", "L2") == "L3":
            continue
        operation_key = _skill_operation_key(skill)
        previous = candidate_by_operation.get(operation_key)
        if previous is None or _known_skill_rank(
            skill,
            successful_run_counts,
        ) > _known_skill_rank(previous, successful_run_counts):
            candidate_by_operation[operation_key] = skill
    candidate_pool = list(candidate_by_operation.values())
    candidate_pool.sort(
        key=lambda skill: (
            -sum(step.kind == "action" for step in skill.steps),
            skill.created_at,
            skill.id,
        )
    )
    # Candidate discovery is optional guidance.  Comparing the current full-size
    # screenshot with every historical prototype made each A2 turn spend minutes
    # before the provider could act, and dark unrelated screens could look close
    # under a global grayscale mean.  The already persisted 3x3 perceptual hashes
    # are a cheap semantic-surface gate; only candidates from the same broad screen
    # family reach the stricter full-image/local-control validation below.
    candidate_state_ids = {current_state_id}
    for skill in candidate_pool:
        candidate_state_ids.update(skill.applicability_scope.required_state_ids)
    region_fingerprint_index = _state_region_fingerprint_index(
        player,
        environment_id,
        state_ids=candidate_state_ids,
    )
    remembered_entry_aliases = KnownRouteProgram(player).remembered_skill_entry_aliases(
        environment_id,
        current_state_id,
    )
    candidate_matches: list[tuple[Any, str | None, float | None]] = []
    visual_match_candidates: list[
        tuple[tuple[int, float, int, int], Any, str, Any | None]
    ] = []
    for skill_index, skill in enumerate(candidate_pool):
        if skill_is_applicable(
            skill,
            environment,
            current_state_id=current_state_id,
            visual_variant_id=None,
        ):
            candidate_matches.append((skill, None, 0.0))
            continue
        for required_state_index, required_state_id in enumerate(
            skill.applicability_scope.required_state_ids
        ):
            if not skill_is_applicable(
                skill,
                environment,
                current_state_id=required_state_id,
                visual_variant_id=None,
            ):
                continue
            if (skill.id, required_state_id) in remembered_entry_aliases:
                candidate_matches.append((skill, required_state_id, 0.0))
                break
            first_action = next(
                (item for item in skill.steps if item.kind == "action"),
                None,
            )
            first_locator = next(
                (
                    locator
                    for locator in skill.locators
                    if first_action is not None and locator.id == first_action.locator_id
                ),
                None,
            )
            region_distance = _state_region_fingerprint_distance(
                region_fingerprint_index,
                current_state_id,
                required_state_id,
            )
            fixed_surface_comparison = bool(
                first_locator is not None
                and first_locator.reference_bounds is not None
                and first_locator.mobility in {"fixed_chrome", "fixed_surface"}
            )
            if region_distance is None or region_distance > 0.12:
                if not fixed_surface_comparison:
                    continue
            visual_match_candidates.append(
                (
                    (
                        0 if region_distance is not None and region_distance <= 0.12 else 1,
                        region_distance if region_distance is not None else float("inf"),
                        skill_index,
                        required_state_index,
                    ),
                    skill,
                    required_state_id,
                    first_locator,
                )
            )
    # Exact state matches and persisted aliases above are cheap and authoritative.
    # Cross-state visual suggestions are optional guidance: rank them by the compact
    # screen-family signature and strictly bound full-image decoding so hundreds of
    # cold candidate operations cannot stall the provider before a real action.
    matched_skill_ids = {skill.id for skill, _alias, _distance in candidate_matches}
    for _priority, skill, required_state_id, first_locator in sorted(
        visual_match_candidates,
        key=lambda item: item[0],
    )[:_OPTIONAL_CANDIDATE_VISUAL_MATCH_LIMIT]:
        if len(candidate_matches) >= limit:
            break
        if skill.id in matched_skill_ids:
            continue
        target_bounds = first_locator.reference_bounds if first_locator is not None else None
        distance = _source_step_state_visual_distance(
            context,
            environment_id=environment_id,
            source_step_id=source_step_id,
            state_id=required_state_id,
            target_bounds=target_bounds,
            prototype_limit=_OPTIONAL_CANDIDATE_PROTOTYPE_LIMIT,
        )
        if (
            distance is None
            and target_bounds is not None
            and _known_route_allows_control_only_entry_alias(skill)
        ):
            distance = _source_step_control_match_distance(
                context,
                environment_id=environment_id,
                source_step_id=source_step_id,
                state_id=required_state_id,
                target_bounds=target_bounds,
                prototype_limit=_OPTIONAL_CANDIDATE_PROTOTYPE_LIMIT,
            )
        if distance is not None:
            candidate_matches.append((skill, required_state_id, distance))
            matched_skill_ids.add(skill.id)
    candidate_matches = candidate_matches[:limit]
    candidates = [item[0] for item in candidate_matches]
    briefs = []
    learned_route_briefs: list[dict[str, Any]] = []
    for skill, source_state_alias_id, source_state_visual_distance in candidate_matches:
        terminal_state_ids = list(
            dict.fromkeys(
                step.expected_state_id
                for step in skill.steps
                if step.kind == "assert" and step.expected_state_id is not None
            )
        )
        visual_variant_id = f"natural-entry.{current_state_id}"
        # A fresh screenshot is not an independent reset.  Keep natural live
        # trials in one conservative cohort; dedicated interruption/reset E2E
        # supplies independently evidenced reset identifiers for promotion.
        independent_reset_id = f"natural-entry.continuity.{environment_id}"
        successful_run_count = successful_run_counts.get(skill.id, 0)
        common = {
            "version_id": skill.id,
            "skill_id": skill.skill_id,
            "title": skill.title,
            "level": skill.level,
            "safety": skill.safety_level,
            "actions": sum(step.kind == "action" for step in skill.steps),
            "terminal_state_ids": terminal_state_ids,
            "existing_run_count": existing_run_counts.get(skill.id, 0),
            "successful_run_count": successful_run_count,
            "source_state_alias_id": source_state_alias_id,
            "source_state_visual_distance": source_state_visual_distance,
        }
        if successful_run_count > 0:
            learned_route_briefs.append(
                {
                    **common,
                    "route_command": (
                        "omni game player --json --agent-brief navigate "
                        f'"{skill.title}" --environment {environment_id} '
                        f"--session {session_id} --source-step {source_step_id}"
                    ),
                }
            )
            continue
        if not _known_route_allows_virgin_candidate_trial(
            player,
            environment_id=environment_id,
            plan=SimpleNamespace(
                skill_version_ids=(skill.id,),
                goal_query=skill.title,
            ),
            uses_entry_alias=source_state_alias_id is not None,
        ):
            continue
        briefs.append(
            {
                **common,
                "trial_command": (
                    "omni game player --json --agent-brief skill replay "
                    f"{skill.id} --environment {environment_id} "
                    f"--session {session_id} --source-step {source_step_id} "
                    + (
                        f"--source-state-alias {source_state_alias_id} "
                        if source_state_alias_id is not None
                        else ""
                    )
                    + (
                        "--allow-candidate "
                        f"--visual-variant {visual_variant_id} "
                        f"--independent-reset {independent_reset_id} "
                        "--baseline-model-input-tokens "
                        f"{max(0, baseline_model_input_tokens)} "
                        "--baseline-decision-latency-ms "
                        f"{max(0.0, baseline_decision_latency_ms):.3f}"
                    )
                ),
            }
        )
    virgin_candidate_ids = {item["version_id"] for item in briefs}
    candidates = [skill for skill in candidates if skill.id in virgin_candidate_ids]
    return candidates, briefs, learned_route_briefs


def _skill_operation_key(skill: Any) -> str:
    """Stable identity for a learned procedure across endpoint state variants."""

    locators = {item.id: item for item in skill.locators}
    procedure: list[dict[str, Any]] = []
    for step in skill.steps:
        if step.kind != "action":
            continue
        locator = locators.get(step.locator_id or "")
        procedure.append(
            {
                "action": step.action.model_dump(mode="json"),
                "bounds": (
                    locator.reference_bounds.model_dump(mode="json")
                    if locator is not None and locator.reference_bounds is not None
                    else None
                ),
            }
        )
    return json.dumps(
        {
            "required_state_ids": sorted(skill.applicability_scope.required_state_ids),
            "procedure": procedure,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _skill_action_payload_key(skill: Any) -> str:
    payload = json.loads(_skill_operation_key(skill))
    payload.pop("required_state_ids", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _successful_skill_run_counts(
    player: AIPlayerStore,
    environment_id: str,
    *,
    skill_runs: Iterable[Any] | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    runs = player.list_skill_runs(environment_id) if skill_runs is None else skill_runs
    for run in runs:
        if (
            run.outcome == "success"
            and run.objective_success
            and run.validation_passed
            and not run.false_success
        ):
            counts[run.skill_version_id] += 1
    return counts


def _known_skill_rank(
    skill: Any,
    successful_run_counts: dict[str, int],
) -> tuple[int, int, int, float, str]:
    status_rank = {
        "preferred": 3,
        "validated": 2,
        "candidate": 1,
    }.get(skill.status, 0)
    successful_runs = successful_run_counts.get(skill.id, 0)
    # Prefer the longest evidence history, then the earliest learned canonical
    # operation.  Negating the timestamp would make the comparison brittle, so
    # use its Unix value with the sign reversed.
    created_at = datetime.fromisoformat(skill.created_at.replace("Z", "+00:00"))
    return (
        status_rank,
        successful_runs,
        len(skill.source_transition_ids),
        -created_at.timestamp(),
        skill.id,
    )


def _rect_containment_overlap(left: SourcePixelRect, right: SourcePixelRect) -> float:
    intersection_width = max(
        0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    intersection_height = max(
        0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    intersection = intersection_width * intersection_height
    smaller = min(left.width * left.height, right.width * right.height)
    return intersection / smaller if smaller > 0 else 0.0


def _is_caller_dependent_skill(skill: Any) -> bool:
    title = str(getattr(skill, "title", ""))
    return any(word in title for word in ("返回", "关闭", "退出"))


def _skill_terminal_state_id(skill: Any) -> str | None:
    terminal = [
        step.expected_state_id
        for step in skill.steps
        if step.kind == "assert" and step.expected_state_id is not None
    ]
    return terminal[-1] if terminal else None


def _known_skill_terminal_reentry_only(skill: Any, state_id: str) -> bool:
    """Reject treating a skill's own terminal stage as an entry visual alias.

    Tutorial overlays and staged controls can keep the same button in the same
    place while changing what the next click means.  Once the exact terminal
    state is already known, it is a distinct learned stage rather than a moving
    variant of the source state.
    """

    required_states = set(skill.applicability_scope.required_state_ids)
    terminal_state_id = _skill_terminal_state_id(skill)
    return state_id not in required_states and state_id == terminal_state_id


def _skill_step_locator(skill: Any, step: Any) -> Any | None:
    return next(
        (locator for locator in skill.locators if locator.id == step.locator_id),
        None,
    )


def _artifact_rgb(path: str | Path) -> np.ndarray[Any, np.dtype[np.uint8]]:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8).copy()


def _guard_fixed_source_pixel_locator(
    context: _PlayerCLIContext,
    *,
    locator: Any,
    source_step_id: str,
    skill_step_id: str,
) -> None:
    """Reject a stale fixed coordinate when its local control has changed.

    A nearly identical full screen can still reorder a crowded navigation strip,
    changing what one source pixel means.  Newer crystallized locators retain the
    exact reference screenshot, so compare the recorded control region with the
    current evidence frame immediately before device access.
    """

    if locator.reference_bounds is None or locator.reference_artifact_id is None:
        return
    facility = context.facility()
    current_step = facility.store.get_evidence_step(source_step_id)
    if current_step is None or current_step.after_frame_id is None:
        raise click.ClickException(
            "fixed_locator_unresolved: current source step has no After screenshot"
        )
    reference_artifact = facility.store.get_artifact(locator.reference_artifact_id)
    current_artifact = facility.store.get_artifact(str(current_step.after_frame_id))
    if reference_artifact is None or not Path(reference_artifact.path).is_file():
        raise click.ClickException("fixed_locator_unresolved: reference screenshot is missing")
    if current_artifact is None or not Path(current_artifact.path).is_file():
        raise click.ClickException("fixed_locator_unresolved: current screenshot is missing")

    local_distance = regional_perceptual_frame_distance(
        reference_artifact.path,
        current_artifact.path,
        locator.reference_bounds,
    )
    structural_distance = regional_structural_frame_distance(
        reference_artifact.path,
        current_artifact.path,
        locator.reference_bounds,
    )
    if local_distance > 0.15 or structural_distance > 0.10:
        raise click.ClickException(
            "fixed_locator_drifted: recorded control no longer matches the current "
            f"source-pixel region for {skill_step_id} "
            f"(visual={local_distance:.6f}, structural={structural_distance:.6f}); "
            "device action rejected"
        )


def _resolve_skill_action_locator(
    context: _PlayerCLIContext,
    *,
    skill: Any,
    step: Any,
    source_step_id: str,
) -> tuple[NormalizedAction, SourcePixelRect | None, dict[str, Any] | None]:
    """Resolve one learned action against the current evidence frame.

    Fixed controls keep their recorded source-pixel geometry. Dynamic world
    objects must be found uniquely in the current frame before the guarded live
    step is created; failure therefore happens before any device action.
    """

    if step.action is None:
        raise click.ClickException(f"skill action step has no action: {step.id}")
    locator = _skill_step_locator(skill, step)
    recorded_bounds = locator.reference_bounds if locator is not None else None
    if locator is None or getattr(locator, "mobility", None) != "dynamic_world_object":
        if (
            locator is not None
            and locator.strategy == "source_pixel"
            and locator.reference_bounds is not None
            and locator.reference_artifact_id is not None
        ):
            _guard_fixed_source_pixel_locator(
                context,
                locator=locator,
                source_step_id=source_step_id,
                skill_step_id=step.id,
            )
        return step.action, recorded_bounds, None
    if (
        locator.strategy != "template"
        or locator.reference_bounds is None
        or locator.reference_artifact_id is None
        or locator.search_region is None
        or locator.match_threshold is None
        or step.action.type != "tap"
        or step.action.x is None
        or step.action.y is None
    ):
        raise click.ClickException(
            f"dynamic_locator_required: incomplete template locator for {step.id}"
        )

    facility = context.facility()
    current_step = facility.store.get_evidence_step(source_step_id)
    if current_step is None or current_step.after_frame_id is None:
        raise click.ClickException(
            "dynamic_locator_unresolved: current source step has no After screenshot"
        )
    reference_artifact = facility.store.get_artifact(locator.reference_artifact_id)
    current_artifact = facility.store.get_artifact(str(current_step.after_frame_id))
    if reference_artifact is None or not Path(reference_artifact.path).is_file():
        raise click.ClickException("dynamic_locator_unresolved: reference screenshot is missing")
    if current_artifact is None or not Path(current_artifact.path).is_file():
        raise click.ClickException("dynamic_locator_unresolved: current screenshot is missing")

    cache = context.runtime.setdefault("dynamic_locator_resolution_cache", {})
    cache_key = (
        skill.id,
        step.id,
        reference_artifact.sha256,
        current_artifact.sha256,
    )
    if isinstance(cache, dict) and cache_key in cache:
        return cache[cache_key]

    started = time.perf_counter()
    reference_rgb = _artifact_rgb(reference_artifact.path)
    current_rgb = _artifact_rgb(current_artifact.path)
    if reference_rgb.shape != current_rgb.shape:
        raise click.ClickException(
            "dynamic_locator_unresolved: reference and current viewport dimensions differ"
        )
    reference_bounds = locator.reference_bounds
    search_region = locator.search_region
    try:
        match = locate_dynamic_template(
            reference_rgb,
            PixelBounds(
                reference_bounds.x,
                reference_bounds.y,
                reference_bounds.width,
                reference_bounds.height,
            ),
            current_rgb,
            search_region=PixelBounds(
                search_region.x,
                search_region.y,
                search_region.width,
                search_region.height,
            ),
            score_threshold=locator.match_threshold,
        )
    except TemplateLocationError as exc:
        scores = ""
        if exc.score is not None:
            scores = f"; best={exc.score:.4f}"
        if exc.second_score is not None:
            scores += f"; second={exc.second_score:.4f}"
        raise click.ClickException(
            f"dynamic_locator_unresolved: {exc}{scores}; device action rejected"
        ) from exc

    resolved_bounds = SourcePixelRect(
        x=match.resolved_bounds.x,
        y=match.resolved_bounds.y,
        width=match.resolved_bounds.width,
        height=match.resolved_bounds.height,
    )
    recorded_point = SourcePixelPoint(x=int(step.action.x), y=int(step.action.y))
    if not reference_bounds.contains(recorded_point):
        raise click.ClickException(
            "dynamic_locator_unresolved: recorded tap is outside its reference bounds"
        )
    offset_x = (recorded_point.x - reference_bounds.x) / max(1, reference_bounds.width - 1)
    offset_y = (recorded_point.y - reference_bounds.y) / max(1, reference_bounds.height - 1)
    resolved_x = resolved_bounds.x + round(offset_x * max(0, resolved_bounds.width - 1))
    resolved_y = resolved_bounds.y + round(offset_y * max(0, resolved_bounds.height - 1))
    resolved_action = step.action.model_copy(update={"x": resolved_x, "y": resolved_y})
    provenance = {
        "strategy": "template",
        "mobility": "dynamic_world_object",
        "skill_version_id": skill.id,
        "skill_step_id": step.id,
        "locator_id": locator.id,
        "reference_artifact_id": reference_artifact.id,
        "reference_artifact_sha256": reference_artifact.sha256,
        "current_artifact_id": current_artifact.id,
        "current_artifact_sha256": current_artifact.sha256,
        "reference_bounds": reference_bounds.model_dump(mode="json"),
        "resolved_bounds": resolved_bounds.model_dump(mode="json"),
        "search_region": search_region.model_dump(mode="json"),
        "score": match.score,
        "second_score": match.second_score,
        "scale": match.scale,
        "match_threshold": locator.match_threshold,
        "recorded_action": step.action.model_dump(mode="json"),
        "resolved_action": resolved_action.model_dump(mode="json"),
        "resolution_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    resolved = (resolved_action, resolved_bounds, provenance)
    if isinstance(cache, dict):
        cache[cache_key] = resolved
    return resolved


def _is_replay_ready_atomic_skill(
    skill: Any,
    *,
    allow_caller_dependent: bool = False,
) -> bool:
    if (
        skill.skill_layer != "atomic"
        or skill.executor_kind != "normalized_actions"
        or skill.status not in {"candidate", "validated", "preferred"}
        or skill.parameters_schema
        or skill.recovery_skill_version_ids
        or skill.safety_level == "restricted"
        # A visible Back/close control may return to different callers while the
        # current screen is pixel-identical.  It needs the persisted navigation
        # stack as an additional parameter; a fixed-endpoint SkillVersion must
        # never guess that caller from pixels alone.
        or (_is_caller_dependent_skill(skill) and not allow_caller_dependent)
    ):
        return False
    action_steps = [step for step in skill.steps if step.kind == "action"]
    assertion_steps = [step for step in skill.steps if step.kind == "assert"]
    if len(action_steps) != 1 or len(assertion_steps) != 1:
        return False
    action_step = action_steps[0]
    assertion_step = assertion_steps[0]
    if (
        assertion_step.expected_state_id is None
        or assertion_step.depends_on_step_ids != [action_step.id]
        or any(
            step.kind not in {"action", "assert"}
            or step.subskill_version_id is not None
            or step.when_true_step_ids
            or step.when_false_step_ids
            for step in skill.steps
        )
    ):
        return False
    locator = _skill_step_locator(skill, action_step)
    fixed_pixel = bool(
        locator is not None
        and locator.strategy == "source_pixel"
        and locator.reference_bounds is not None
        and getattr(locator, "mobility", None) != "dynamic_world_object"
    )
    dynamic_template = bool(
        locator is not None
        and locator.strategy == "template"
        and getattr(locator, "mobility", None) == "dynamic_world_object"
        and locator.reference_bounds is not None
        and locator.reference_artifact_id is not None
        and locator.search_region is not None
        and locator.match_threshold is not None
        and action_step.action is not None
        and action_step.action.type == "tap"
    )
    return fixed_pixel or dynamic_template


def _is_strict_virgin_atomic_candidate(
    player: AIPlayerStore,
    *,
    environment_id: str,
    skill: Any,
    allow_caller_dependent: bool,
) -> bool:
    """Return whether one first-use skill is eligible for its sole validation trial."""

    return bool(
        getattr(skill, "status", None) == "candidate"
        and getattr(skill, "source_transition_ids", None)
        and getattr(skill, "evidence_refs", None)
        and _is_replay_ready_atomic_skill(
            skill,
            allow_caller_dependent=allow_caller_dependent,
        )
        and not player.list_skill_runs(
            environment_id,
            skill_version_id=skill.id,
        )
    )


def _learned_reverse_navigation_skill_id(
    player: AIPlayerStore,
    environment_id: str,
    *,
    caller_state_id: str,
    entered_state_id: str,
) -> str | None:
    """Find one evidence-backed child -> caller operation without global closure."""

    program = KnownRouteProgram(player)
    proven_skill_ids = {
        arc.skill_version_id
        for arc in program.arcs(
            environment_id,
            require_successful_run=True,
        )
    }
    direct_entry_aliases = program.remembered_skill_entry_aliases(
        environment_id,
        entered_state_id,
        require_settled_proof=True,
    )
    caller_equivalents = {
        caller_state_id,
        *program.entry_bridge_peers(
            environment_id,
            caller_state_id,
            require_successful_run=True,
        ),
    }
    latest_by_skill: dict[str, Any] = {}
    for candidate in player.list_skill_versions(environment_id):
        previous = latest_by_skill.get(candidate.skill_id)
        if previous is None or candidate.version > previous.version:
            latest_by_skill[candidate.skill_id] = candidate
    settled_candidates = [
        candidate
        for candidate in latest_by_skill.values()
        if candidate.id in proven_skill_ids
        if _is_caller_dependent_skill(candidate)
        and _is_replay_ready_atomic_skill(candidate, allow_caller_dependent=True)
        and (
            entered_state_id in candidate.applicability_scope.required_state_ids
            or any(
                skill_version_id == candidate.id
                and required_state_id in candidate.applicability_scope.required_state_ids
                for skill_version_id, required_state_id in direct_entry_aliases
            )
        )
        and _skill_terminal_state_id(candidate) in caller_equivalents
    ]
    settled_candidate_ids = {candidate.id for candidate in settled_candidates}
    if len(settled_candidate_ids) == 1:
        return next(iter(settled_candidate_ids))
    if settled_candidate_ids:
        return None

    # A raw first return already supplies the candidate's exact child -> caller
    # edge and immutable evidence. Retain one such untouched candidate in the
    # round-trip frame so the second use can validate it deterministically. Do
    # not use state aliases or caller bridges until a SkillRun has succeeded.
    virgin_candidates = [
        candidate
        for candidate in latest_by_skill.values()
        if _is_strict_virgin_atomic_candidate(
            player,
            environment_id=environment_id,
            skill=candidate,
            allow_caller_dependent=True,
        )
        and _is_caller_dependent_skill(candidate)
        and entered_state_id in candidate.applicability_scope.required_state_ids
        and _skill_terminal_state_id(candidate) == caller_state_id
    ]
    virgin_candidate_ids = {candidate.id for candidate in virgin_candidates}
    return next(iter(virgin_candidate_ids)) if len(virgin_candidate_ids) == 1 else None


def _known_atomic_action_matches(
    skill: Any,
    action: NormalizedAction,
    target_bounds: SourcePixelRect | None,
) -> bool:
    action_steps = [step for step in skill.steps if step.kind == "action"]
    if len(action_steps) != 1:
        return False
    recorded = action_steps[0]
    if recorded.action is None or recorded.action.type != action.type:
        return False
    if action.type == "tap":
        if None in (recorded.action.x, recorded.action.y, action.x, action.y):
            return False
        locator = next(
            (item for item in skill.locators if item.id == recorded.locator_id),
            None,
        )
        if locator is not None and getattr(locator, "mobility", None) == "dynamic_world_object":
            requested_point = SourcePixelPoint(x=int(action.x), y=int(action.y))
            # This is only a preliminary candidate filter. The takeover path then
            # resolves the template and proves that the Agent's requested current
            # instance overlaps that resolved instance before selecting the skill.
            return bool(
                locator.strategy == "template"
                and target_bounds is not None
                and target_bounds.contains(requested_point)
            )
        recorded_bounds = locator.reference_bounds if locator is not None else None
        if recorded_bounds is None or target_bounds is None:
            return recorded.action == action
        recorded_point = SourcePixelPoint(
            x=int(recorded.action.x),
            y=int(recorded.action.y),
        )
        requested_point = SourcePixelPoint(x=int(action.x), y=int(action.y))
        tolerance_x = max(8.0, recorded_bounds.width * 0.2)
        tolerance_y = max(8.0, recorded_bounds.height * 0.2)
        return (
            recorded_bounds.contains(recorded_point)
            and recorded_bounds.contains(requested_point)
            and target_bounds.contains(recorded_point)
            and target_bounds.contains(requested_point)
            and abs(recorded_point.x - requested_point.x) <= tolerance_x
            and abs(recorded_point.y - requested_point.y) <= tolerance_y
            and _rect_containment_overlap(recorded_bounds, target_bounds) >= 0.80
        )
    return recorded.action == action


def _dynamic_takeover_request_matches_resolution(
    requested_action: NormalizedAction,
    requested_bounds: SourcePixelRect | None,
    resolved_action: NormalizedAction,
    resolved_bounds: SourcePixelRect,
) -> bool:
    if (
        requested_action.type != "tap"
        or resolved_action.type != "tap"
        or requested_action.x is None
        or requested_action.y is None
        or resolved_action.x is None
        or resolved_action.y is None
        or requested_bounds is None
    ):
        return False
    requested_point = SourcePixelPoint(
        x=int(requested_action.x),
        y=int(requested_action.y),
    )
    resolved_point = SourcePixelPoint(x=int(resolved_action.x), y=int(resolved_action.y))
    tolerance_x = max(8.0, resolved_bounds.width * 0.25)
    tolerance_y = max(8.0, resolved_bounds.height * 0.25)
    return bool(
        resolved_bounds.contains(requested_point)
        and requested_bounds.contains(resolved_point)
        and abs(requested_point.x - resolved_point.x) <= tolerance_x
        and abs(requested_point.y - resolved_point.y) <= tolerance_y
        and _rect_containment_overlap(resolved_bounds, requested_bounds) >= 0.50
    )


def _known_skill_title_matches_target(skill: Any, target_name: str) -> bool:
    """Use the caller's explicit goal to disambiguate identical screen controls."""

    def normalized(value: str) -> str:
        return re.sub(r"[\s\-_/·→:：()（）]+", "", value).casefold()

    title = normalized(str(skill.title))
    target = normalized(target_name)
    if not title or not target:
        return False
    return title == target or (
        min(len(title), len(target)) >= 2 and (title in target or target in title)
    )


_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY = "known_skill_goal_alias_request"


def _remember_successful_skill_goal_alias(
    player: AIPlayerStore,
    *,
    environment_id: str,
    skill: Any,
    run: Any,
    goal_alias: str,
    provenance: str,
    evidence_refs: list[EvidenceReferenceV1],
) -> MemoryRecordV1 | None:
    """Bind one exact semantic phrase to one objectively successful fixed skill."""

    goal_alias = goal_alias.strip()
    if (
        provenance not in {"guarded_atomic_takeover", "navigation_frame_return", "known_route"}
        or not goal_alias
        or len(goal_alias) > 160
        or getattr(run, "outcome", None) != "success"
        or not getattr(run, "objective_success", False)
        or not getattr(run, "validation_passed", False)
        or getattr(run, "false_success", False)
        or int(getattr(run, "safety_violation_count", 0)) > 0
    ):
        return None
    if provenance == "guarded_atomic_takeover" and not _known_skill_title_matches_target(
        skill, goal_alias
    ):
        return None
    normalized_alias = _exact_goal_alias_key(goal_alias)
    if not normalized_alias:
        return None
    remembered = KnownRouteProgram(player).remembered_skill_goal_aliases(
        environment_id,
        skill.id,
    )
    if any(_exact_goal_alias_key(candidate) == normalized_alias for candidate in remembered):
        return None
    identity = hashlib.sha256(
        "\n".join([skill.id, normalized_alias, run.id]).encode("utf-8")
    ).hexdigest()[:24]
    memory = MemoryRecordV1(
        id=f"memory.known-skill-goal-alias.{identity}",
        environment_id=environment_id,
        kind="procedural",
        subject_id=f"known-skill-goal-alias:{skill.id}",
        payload={
            "schema": "game-observatory.ai-player.known-skill-goal-alias.v1",
            "skill_version_id": skill.id,
            "goal_alias": goal_alias,
            "successful_run_id": run.id,
            "requires_settled_run": True,
            "provenance": provenance,
            "rule": "仅当目标短语精确匹配时，选择这条已成功的固定技能。",
        },
        evidence_refs=evidence_refs,
    )
    if player.get_memory(environment_id, memory.id) is None:
        player.append_memory(memory)
        return memory
    return None


def _automatic_replay_baseline(
    context: _PlayerCLIContext,
    session_id: str,
) -> tuple[int, float]:
    external = _external_ledger(context).get_session(session_id)
    if external is None or external.invocation_count <= 0:
        return 0, 0.0
    return (
        max(1, round(external.input_tokens / external.invocation_count)),
        max(
            1.0,
            external.total_duration_seconds * 1000 / external.invocation_count,
        ),
    )


def _candidate_trial_baseline(external: Any) -> tuple[int, float]:
    """Return measured per-action cost, or zero when this generation has no sample."""

    action_count = int(external.semantic_action_count)
    if action_count <= 0:
        return 0, 0.0
    uncached_input_tokens = max(
        0,
        int(external.input_tokens)
        - int(external.cached_input_tokens)
        - int(external.cache_creation_input_tokens),
    )
    return (
        max(1, round(uncached_input_tokens / action_count)),
        max(1.0, float(external.total_duration_seconds) * 1000 / action_count),
    )


def _try_replay_known_atomic_action(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    action: NormalizedAction,
    target_bounds: SourcePixelRect | None,
    target_name: str,
) -> bool:
    """Let the deterministic layer take over a semantic Agent's repeated click."""

    takeover_started = time.perf_counter()
    phase_started = takeover_started
    takeover_timings: dict[str, float] = {}
    try:
        environment, _run, _step, source_reference = _evidence_reference_from_step(
            context,
            environment_id=environment_id,
            step_id=source_step_id,
            require_semantic_source=False,
        )
        # A repeated semantic click should not run the full state-induction
        # pipeline merely to discover whether a matching operation exists.  Use
        # an already persisted assignment when present; otherwise the operation's
        # local control guard below is the bounded deterministic entry check.
        assignment = _state_assignment_for_evidence(
            context.player(),
            environment_id=environment_id,
            evidence_refs=[source_reference],
        )
        takeover_timings["source_and_assignment"] = round(
            (time.perf_counter() - phase_started) * 1000,
            3,
        )
    except (KeyError, TypeError, ValueError):
        return False
    phase_started = time.perf_counter()
    player = context.player()
    navigation_stack = player.get_navigation_stack(environment_id, session_id)
    active_navigation_frame = (
        navigation_stack.frames[-1]
        if navigation_stack is not None
        and navigation_stack.frames
        and navigation_stack.current_state_id == navigation_stack.frames[-1].entered_state_id
        else None
    )
    latest_by_skill: dict[str, Any] = {}
    for skill in player.list_skill_versions(environment_id):
        previous = latest_by_skill.get(skill.skill_id)
        if previous is None or skill.version > previous.version:
            latest_by_skill[skill.skill_id] = skill
    successful_run_counts = _successful_skill_run_counts(player, environment_id)

    def has_replay_proof(skill: Any) -> bool:
        if successful_run_counts.get(skill.id, 0) > 0:
            return True
        return bool(
            active_navigation_frame is not None
            and assignment is not None
            and skill.id == active_navigation_frame.return_skill_version_id
            and assignment.state_id == active_navigation_frame.entered_state_id
            and assignment.state_id in skill.applicability_scope.required_state_ids
            and _skill_terminal_state_id(skill) == active_navigation_frame.caller_state_id
            and _is_strict_virgin_atomic_candidate(
                player,
                environment_id=environment_id,
                skill=skill,
                allow_caller_dependent=True,
            )
        )

    possible = [
        skill
        for skill in latest_by_skill.values()
        if has_replay_proof(skill)
        and _is_replay_ready_atomic_skill(
            skill,
            allow_caller_dependent=active_navigation_frame is not None,
        )
        and _known_atomic_action_matches(skill, action, target_bounds)
        and (
            not _is_caller_dependent_skill(skill)
            or (
                active_navigation_frame is not None
                and (
                    skill.id == active_navigation_frame.return_skill_version_id
                    or (
                        active_navigation_frame.entered_state_id
                        in skill.applicability_scope.required_state_ids
                        and _skill_terminal_state_id(skill)
                        == active_navigation_frame.caller_state_id
                    )
                )
            )
        )
    ]
    if not possible:
        return False
    title_matches = [
        skill for skill in possible if _known_skill_title_matches_target(skill, target_name)
    ]
    goal_alias_provenance = "guarded_atomic_takeover"
    if title_matches:
        possible = title_matches
    else:
        settled_route_skill_ids = {
            arc.skill_version_id
            for arc in KnownRouteProgram(player).arcs(
                environment_id,
                require_successful_run=True,
            )
        }
        frame_return_matches = [
            skill
            for skill in possible
            if active_navigation_frame is not None
            and skill.id == active_navigation_frame.return_skill_version_id
            and (
                skill.id in settled_route_skill_ids
                or _is_strict_virgin_atomic_candidate(
                    player,
                    environment_id=environment_id,
                    skill=skill,
                    allow_caller_dependent=True,
                )
            )
        ]
        if len(frame_return_matches) == 1:
            # The active round-trip frame names one exact reverse operation.
            # Action/bounds have already matched above; the source entry guard
            # below must still prove this live child surface before any click.
            possible = frame_return_matches
            goal_alias_provenance = "navigation_frame_return"
        else:
            # Identical controls are common across game panels (for example every
            # building's "upgrade" button).  A pixel/action match without a semantic
            # goal match or one exact round-trip return must remain guarded first-use.
            return False
    takeover_timings["skill_index"] = round(
        (time.perf_counter() - phase_started) * 1000,
        3,
    )

    phase_started = time.perf_counter()
    known_aliases = (
        KnownRouteProgram(player).remembered_skill_entry_aliases(
            environment_id,
            assignment.state_id,
            require_settled_proof=(goal_alias_provenance == "navigation_frame_return"),
        )
        if assignment is not None
        else set()
    )
    matches: list[tuple[Any, str | None]] = []
    for skill in possible:
        action_step = next(step for step in skill.steps if step.kind == "action")
        first_locator = _skill_step_locator(skill, action_step)
        if (
            first_locator is not None
            and getattr(first_locator, "mobility", None) == "dynamic_world_object"
        ):
            try:
                resolved_action, resolved_bounds, _provenance = _resolve_skill_action_locator(
                    context,
                    skill=skill,
                    step=action_step,
                    source_step_id=source_step_id,
                )
            except click.ClickException:
                continue
            if resolved_bounds is None or not _dynamic_takeover_request_matches_resolution(
                action,
                target_bounds,
                resolved_action,
                resolved_bounds,
            ):
                continue
        required_states = skill.applicability_scope.required_state_ids
        if assignment is not None and assignment.state_id in required_states:
            matches.append((skill, None))
            continue
        if assignment is not None and _known_skill_terminal_reentry_only(
            skill,
            assignment.state_id,
        ):
            # The current frame is the already learned result of this skill.
            # Repeating a visually identical control here is a new transition
            # and must be observed semantically once before it can be replayed.
            continue
        remembered = next(
            (
                required_state_id
                for required_state_id in required_states
                if (skill.id, required_state_id) in known_aliases
            ),
            None,
        )
        if remembered is not None:
            verified = context.runtime.setdefault("verified_skill_entry_aliases", set())
            if isinstance(verified, set):
                verified.add((skill.id, remembered))
            matches.append((skill, remembered))
            continue
        # State induction may split a moving/account-dependent surface into a
        # fresh ID.  At this point the action and locator have already matched a
        # known atomic operation, so compare only that operation's source
        # control against its recorded source state.  A successful replay writes
        # a procedural alias; subsequent visits become a pure database lookup.
        if (
            first_locator is not None
            and getattr(first_locator, "mobility", None) == "dynamic_world_object"
        ):
            # A target template proves where an object is; it does not prove that
            # an unassigned/changed overlay is the same interaction stage.
            continue
        for required_state_id in required_states:
            if first_locator is None or first_locator.reference_bounds is None:
                continue
            distance = _source_step_control_match_distance(
                context,
                environment_id=environment_id,
                source_step_id=source_step_id,
                state_id=required_state_id,
                target_bounds=first_locator.reference_bounds,
            )
            if distance is None:
                continue
            if assignment is None:
                # Keep the canonical source state as an in-memory checkpoint for
                # this one replay.  The live Before/source guard still runs before
                # the click, so bypassing state induction does not bypass device
                # drift protection.
                context.runtime["known_route_checkpoint"] = {
                    "environment_id": environment_id,
                    "source_step_id": source_step_id,
                    "state_id": required_state_id,
                }
                matches.append((skill, None))
            else:
                verified = context.runtime.setdefault("verified_skill_entry_aliases", set())
                if isinstance(verified, set):
                    verified.add((skill.id, required_state_id))
                matches.append((skill, required_state_id))
            break
    if not matches:
        return False
    takeover_timings["entry_match"] = round(
        (time.perf_counter() - phase_started) * 1000,
        3,
    )
    phase_started = time.perf_counter()
    operation_groups: dict[str, list[tuple[Any, str | None]]] = defaultdict(list)
    for match in matches:
        operation_groups[_skill_action_payload_key(match[0])].append(match)
    if len(operation_groups) != 1:
        return False
    selected, source_alias_id = max(
        next(iter(operation_groups.values())),
        key=lambda item: _known_skill_rank(item[0], successful_run_counts),
    )
    baseline_tokens, baseline_latency = _automatic_replay_baseline(context, session_id)
    replay_callback = getattr(player_skill_replay.callback, "__wrapped__", None)
    if not callable(replay_callback):
        return False
    takeover_timings["selection"] = round(
        (time.perf_counter() - phase_started) * 1000,
        3,
    )
    takeover_timings["takeover_preflight_total"] = round(
        (time.perf_counter() - takeover_started) * 1000,
        3,
    )
    context.runtime["known_takeover_timings_ms"] = takeover_timings
    previous_alias_request = context.runtime.get(_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY)
    context.runtime[_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY] = {
        "skill_version_id": selected.id,
        "goal_alias": target_name,
        "provenance": goal_alias_provenance,
    }
    try:
        replay_callback(
            context,
            skill_id=selected.id,
            environment_id=environment.id,
            session_id=session_id,
            source_step_id=source_step_id,
            source_state_alias_id=source_alias_id,
            allow_candidate=selected.status != "preferred" or source_alias_id is not None,
            visual_variant_id=(
                f"natural-entry.{assignment.state_id}"
                if assignment is not None
                else f"natural-entry.{source_step_id}"
            ),
            independent_reset_id=f"natural-entry.{source_step_id}",
            baseline_model_input_tokens=baseline_tokens,
            baseline_decision_latency_ms=baseline_latency,
            max_safety="economic",
        )
    finally:
        if previous_alias_request is None:
            context.runtime.pop(_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY, None)
        else:
            context.runtime[_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY] = previous_alias_request
    return True


def _source_step_state_visual_distance(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    source_step_id: str,
    state_id: str,
    target_bounds: SourcePixelRect | None = None,
    allow_layout_variant: bool = False,
    prototype_limit: int = 8,
) -> float | None:
    """Compare one current frame with recent screenshot prototypes of a state."""

    observatory = context.facility().store
    step = observatory.get_evidence_step(source_step_id)
    if step is None or not step.after_frame_id:
        return None
    source = observatory.get_artifact(str(step.after_frame_id))
    if source is None or source.kind != "screenshot" or not Path(source.path).is_file():
        return None
    player = context.player()
    comparison_paths = [
        Path(artifact.path)
        for artifact in player.list_recent_state_screenshot_prototypes(
            environment_id,
            state_id,
            limit=prototype_limit,
        )
        if Path(artifact.path).is_file()
    ]
    comparison_paths = list(dict.fromkeys(comparison_paths))
    if not comparison_paths:
        return None
    # Recent verified prototypes are the best match for live animation and account
    # state.  A single accepted prototype proves applicability; decoding every older
    # full-resolution frame only adds latency to an already known route.
    for comparison_path in comparison_paths:
        global_distance = perceptual_frame_distance(source.path, comparison_path)
        if global_distance <= 0.012:
            return global_distance
        # A learned screen remains the same semantic surface when only account
        # progress, timers, map routes, or other live content changes.  Require
        # both a narrowly bounded visual delta and stable full-screen edge
        # structure so a popup or a different caller screen still fails closed.
        if (
            global_distance <= 0.03
            and structural_frame_distance(source.path, comparison_path) <= 0.07
        ):
            return global_distance
        if allow_layout_variant and global_distance <= 0.12:
            full_hash_similarity, region_hash_similarities = frame_layout_hash_similarities(
                source.path, comparison_path
            )
            # This fallback is reserved for a declared known-route endpoint.
            # Eight stable thirds plus a high aggregate layout score admit the
            # observed season/map-content variant while a popup or wrong caller
            # still fails closed.  Entry recognition never enables this branch.
            if (
                full_hash_similarity >= 0.68
                and sum(region_hash_similarities) / len(region_hash_similarities) >= 0.88
                and sum(score >= 0.85 for score in region_hash_similarities) >= 8
            ):
                return global_distance
        if target_bounds is not None:
            local_distance = regional_perceptual_frame_distance(
                source.path,
                comparison_path,
                target_bounds,
            )
            # Moving character art can change pixels substantially while the
            # surface chrome remains identical.  Admit that variant only when
            # the whole frame still belongs to the same broad surface family
            # and the target region's structure is stable.  This rejects the
            # visually similar Back action that returned to a different caller
            # (observed global distance 0.207) while accepting hero-list motion
            # variants (0.063-0.085 global, 0.008 structural).
            if global_distance <= 0.10:
                structural_distance = regional_structural_frame_distance(
                    source.path,
                    comparison_path,
                    target_bounds,
                )
                if structural_distance <= 0.08:
                    return local_distance
    return None


def _terminal_observation_receipt(
    *,
    terminal_reference: EvidenceReferenceV1,
    observed_state_id: str | None,
    expected_state_id: str | None,
    visual_distance: float | None,
    summary: str,
) -> GuardedSkillObservationReceiptV1:
    """Carry a matched terminal visual guard into the structured assert.

    Terminal ingestion can conservatively induce a fresh state for a dynamic
    screen even after the replay guard matched a confirmed prototype or alias.
    The assert must retain that evidence-bound visual decision; otherwise the
    correct fixed action is recorded as interrupted merely because the induced
    assignment uses a newer screenshot state id.
    """

    return GuardedSkillObservationReceiptV1(
        evidence_refs=[terminal_reference],
        observed_state_id=observed_state_id,
        verified_state_guard=bool(
            expected_state_id is not None
            and observed_state_id == expected_state_id
            and visual_distance is not None
        ),
        summary=summary,
    )


def _source_step_control_match_distance(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    source_step_id: str,
    state_id: str,
    target_bounds: SourcePixelRect,
    prototype_limit: int = 8,
) -> float | None:
    """Match a known control across different camera/content backgrounds."""

    observatory = context.facility().store
    step = observatory.get_evidence_step(source_step_id)
    if step is None or not step.after_frame_id:
        return None
    source = observatory.get_artifact(str(step.after_frame_id))
    if source is None or source.kind != "screenshot" or not Path(source.path).is_file():
        return None
    for artifact in context.player().list_recent_state_screenshot_prototypes(
        environment_id,
        state_id,
        limit=prototype_limit,
    ):
        if not Path(artifact.path).is_file():
            continue
        local_distance = regional_perceptual_frame_distance(
            source.path,
            artifact.path,
            target_bounds,
        )
        if local_distance > 0.15:
            continue
        structural_distance = regional_structural_frame_distance(
            source.path,
            artifact.path,
            target_bounds,
        )
        # The stored target rectangle may include the animated map/chat edge
        # around a stable large control.  A 0.10 structural bound admits the
        # observed city/outside variants of the same labeled control (0.087)
        # while action, bounds, explicit target title and terminal state remain
        # independent guards.
        if structural_distance <= 0.10:
            return local_distance
    return None


def _known_route_entry_match_distance(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    source_step_id: str,
    entry_state_id: str,
    skill_version_ids: tuple[str, ...],
    allow_control_fallback: bool = True,
) -> float | None:
    """Match a route entry by its full screen or its first guarded control."""

    skill = (
        context.player().get_skill_version_by_id(
            environment_id,
            skill_version_ids[0],
        )
        if skill_version_ids
        else None
    )
    action_step = (
        next(
            (step for step in skill.steps if step.kind == "action" and step.locator_id is not None),
            None,
        )
        if skill is not None
        else None
    )
    locator = (
        next(
            (item for item in skill.locators if item.id == action_step.locator_id),
            None,
        )
        if skill is not None and action_step is not None
        else None
    )
    global_distance = _source_step_state_visual_distance(
        context,
        environment_id=environment_id,
        source_step_id=source_step_id,
        state_id=entry_state_id,
        target_bounds=(locator.reference_bounds if locator is not None else None),
    )
    if global_distance is not None:
        return global_distance
    if not allow_control_fallback or skill is None:
        return None
    if locator is None or locator.reference_bounds is None:
        return None
    return _source_step_control_match_distance(
        context,
        environment_id=environment_id,
        source_step_id=source_step_id,
        state_id=entry_state_id,
        target_bounds=locator.reference_bounds,
    )


def _known_route_allows_control_only_entry_alias(skill: Any) -> bool:
    """Limit new local-control aliases to low-impact operations.

    A map tile, building or unit can occupy the same rectangle while its
    business state changes. Full-screen state matches and already persisted
    aliases remain eligible at every permitted safety level. A brand-new alias
    based only on one similar control is reserved for read-only or reversible
    paths whose destination does not depend on their caller. Back, close and exit
    controls need exact state or proven round-trip context; visually identical
    buttons can return to different parent pages.
    """

    return skill.safety_level in {"read_only", "reversible"} and not _is_caller_dependent_skill(
        skill
    )


def _known_route_region_gate_allows_entry_comparison(
    region_distance: float | None,
    entry_skill: Any | None,
) -> bool:
    """Allow precise local-control proof when dynamic content defeats screen hashes.

    The 3x3 screen-family hash remains the normal cheap gate.  A missing or
    distant family signature may proceed only to a fixed-surface
    broad-frame-plus-local comparison, or to the independently guarded
    low-impact control fallback.  One of those stricter proofs must still pass
    before any device action is possible.
    """

    if region_distance is not None and region_distance <= 0.12:
        return True
    if entry_skill is not None:
        action_step = next(
            (
                step
                for step in entry_skill.steps
                if step.kind == "action" and step.locator_id is not None
            ),
            None,
        )
        locator = next(
            (
                item
                for item in entry_skill.locators
                if action_step is not None and item.id == action_step.locator_id
            ),
            None,
        )
        if (
            locator is not None
            and locator.reference_bounds is not None
            and locator.mobility in {"fixed_chrome", "fixed_surface"}
        ):
            return True
    return bool(
        entry_skill is not None and _known_route_allows_control_only_entry_alias(entry_skill)
    )


def _known_route_allows_virgin_candidate_trial(
    player: AIPlayerStore,
    *,
    environment_id: str,
    plan: Any,
    uses_entry_alias: bool,
) -> bool:
    """Admit one guarded second-use trial without calling the semantic Agent again.

    A newly crystallized atomic candidate represents a real successful first
    interaction, but its title still came from the exploring Agent.  The next
    request for the same goal may therefore execute it once in validation mode;
    a successful SkillRun makes it a warm route and any recorded failure removes
    it from the fixed graph.  Multi-step, previously attempted, provenance-free,
    or caller-dependent alias trials stay under explicit semantic control.
    """

    skill_version_ids = tuple(getattr(plan, "skill_version_ids", ()))
    if len(skill_version_ids) != 1:
        return False
    skill = player.get_skill_version_by_id(environment_id, skill_version_ids[0])
    if skill is None or not _is_strict_virgin_atomic_candidate(
        player,
        environment_id=environment_id,
        skill=skill,
        allow_caller_dependent=not uses_entry_alias,
    ):
        return False
    terminal_state_id = _skill_terminal_state_id(skill)
    if terminal_state_id is None or KnownRouteProgram(player).terminal_state_contradicts_goal(
        environment_id,
        terminal_state_id,
        str(getattr(plan, "goal_query", "")),
    ):
        return False
    return True


def _known_route_has_proven_round_trip_entry(
    player: AIPlayerStore,
    *,
    environment_id: str,
    entry_skill: Any,
    observed_state_id: str,
) -> bool:
    """Recognize a returned caller after both directions succeeded once."""

    entered_state_id = _skill_terminal_state_id(entry_skill)
    if entered_state_id is None:
        return False
    successful_runs = _successful_skill_run_counts(player, environment_id)
    if successful_runs.get(entry_skill.id, 0) < 1:
        return False
    latest_by_skill: dict[str, Any] = {}
    for candidate in player.list_skill_versions(environment_id):
        previous = latest_by_skill.get(candidate.skill_id)
        if previous is None or candidate.version > previous.version:
            latest_by_skill[candidate.skill_id] = candidate
    return any(
        _is_caller_dependent_skill(candidate)
        and _is_replay_ready_atomic_skill(candidate, allow_caller_dependent=True)
        and entered_state_id in candidate.applicability_scope.required_state_ids
        and _skill_terminal_state_id(candidate) == observed_state_id
        and successful_runs.get(candidate.id, 0) >= 1
        for candidate in latest_by_skill.values()
    )


def _known_route_verified_entry_aliases(
    program: KnownRouteProgram,
    *,
    environment_id: str,
    observed_state_id: str,
    first_skill_version_id: str,
    visually_verified_entry_state_ids: Sequence[str],
) -> set[tuple[str, str]]:
    """Combine durable aliases with this invocation's already guarded entry match."""

    verified = set(
        program.remembered_skill_entry_aliases(
            environment_id,
            observed_state_id,
        )
    )
    verified.update(
        (first_skill_version_id, state_id) for state_id in visually_verified_entry_state_ids
    )
    return verified


def _known_route_required_state_alias(
    program: KnownRouteProgram,
    *,
    environment_id: str,
    observed_state_id: str,
    skill: Any,
    max_safety: str = "economic",
) -> str | None:
    """Resolve a direct alias or one evidence-backed intermediate bridge."""

    bridge_resolver = getattr(program, "required_state_alias", None)
    if callable(bridge_resolver):
        return bridge_resolver(
            environment_id,
            observed_state_id,
            skill,
            max_safety=max_safety,
            require_successful_run=True,
        )

    # Lightweight external adapters and older fixture programs expose only the
    # durable direct-alias query.  Preserve that exact contract; the new
    # evidence-backed intermediate bridge remains unavailable until the full
    # KnownRouteProgram is present.
    declared = set(skill.applicability_scope.required_state_ids)
    matches = {
        required_state_id
        for skill_version_id, required_state_id in program.remembered_skill_entry_aliases(
            environment_id,
            observed_state_id,
        )
        if skill_version_id == skill.id and required_state_id in declared
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _state_prototypes_require_motion_terminal(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    state_id: str,
) -> bool:
    paths = [
        Path(artifact.path)
        for artifact in context.player().list_recent_state_screenshot_prototypes(
            environment_id,
            state_id,
            limit=4,
        )
        if Path(artifact.path).is_file()
    ]
    paths = list(dict.fromkeys(paths))
    for left, right in zip(paths, paths[1:]):
        distance = perceptual_frame_distance(left, right)
        if 0.012 < distance <= 0.15:
            return True
    return False


_ACTION_EXPECTATION_MET_REASON_PREFIX = "动作完成并通过预期检查："
_ACTION_EXPECTATION_MISSED_REASON_PREFIX = "动作完成但未满足预期，建议停止："


_KNOWN_ROUTE_LEASE_RUNTIME_MARGIN_SECONDS = 90.0


def _known_route_lease_covers_action(session: Any) -> bool:
    """Return whether a verified route can finish before its worker lease expires."""

    if not session.lease_id or not session.lease_holder or not session.lease_expires_at:
        return False
    try:
        expires_at = datetime.fromisoformat(session.lease_expires_at.replace("Z", "+00:00"))
        checked_at = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
    except ValueError:
        return False
    return (expires_at - checked_at).total_seconds() >= _KNOWN_ROUTE_LEASE_RUNTIME_MARGIN_SECONDS


def _execute_guarded_action(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    action: NormalizedAction,
    target_name: str,
    target_bounds: SourcePixelRect | None,
    expectation_summary: str,
    expect_change: bool,
    min_visual_distance: float,
    dynamic_scene: bool = False,
    fast_known_route: bool = False,
    source_semantic_state_id: str | None = None,
    source_state_observation_id: str | None = None,
    expected_semantic_state_id: str | None = None,
    skill_replay_version_id: str | None = None,
    locator_resolution: dict[str, Any] | None = None,
    route_runtime: LiveStepRouteRuntime | None = None,
) -> dict[str, Any]:
    environment, run, source_step, reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=source_step_id,
        require_semantic_source=target_bounds is not None,
    )
    player = context.player()
    source_artifacts = [
        artifact
        for artifact_id in reference.artifact_ids
        if (artifact := context.facility().store.get_artifact(artifact_id)) is not None
    ]
    surface_profile_pair = resolve_reusable_semantic_surface_profile_pair(
        player,
        environment.id,
        source_step=source_step,
        source_artifacts=source_artifacts,
        source_observation_id=source_state_observation_id,
        source_state_id=source_semantic_state_id,
        expected_state_id=expected_semantic_state_id,
    )
    # A first-time click can still start from an already understood animated
    # screen (for example a hero detail page with an idle character loop).  In
    # that case the whole frame may never become pixel-static even though the
    # tapped control and the resulting panel are stable.  Reuse the persisted
    # source-state assignment to select the bounded-motion terminal gate; this
    # keeps semantic exploration for the new edge while avoiding a false failed
    # receipt that would prevent the successful edge from becoming a skill.
    if target_bounds is not None and action.type in _POINTER_ACTION_TYPES:
        motion_source_state_id = source_semantic_state_id
        if motion_source_state_id is None:
            source_assignment = _state_assignment_for_evidence(
                context.player(),
                environment_id=environment_id,
                evidence_refs=[reference],
            )
            if source_assignment is not None:
                motion_source_state_id = source_assignment.state_id
        if (
            not dynamic_scene
            and not fast_known_route
            and motion_source_state_id is not None
            and _state_prototypes_require_motion_terminal(
                context,
                environment_id=environment_id,
                state_id=motion_source_state_id,
            )
        ):
            dynamic_scene = True
    control = AIPlayerSessionControl(context.player())
    session = control.assert_session_can_act(environment.id, session_id)
    # Known-route execution already has a lease guard at entry, inside the
    # DeviceGateway reservation and again at the terminal checkpoint. When the
    # current lease safely covers the bounded action window, another standalone
    # heartbeat only adds a write transaction before the same fixed click.
    # Near expiry we retain the original renewal path.
    lease_covers_known_action = fast_known_route and _known_route_lease_covers_action(session)
    if (
        session.lease_id is not None
        and session.lease_holder is not None
        and not lease_covers_known_action
    ):
        session = control.heartbeat(
            session.id,
            AIPlayerSessionHeartbeatCommand(
                command_id=(f"{session.id}.action-start.{session.version}.{uuid.uuid4().hex[:12]}"),
                environment_id=environment.id,
                expected_version=session.version,
                actor="ai-player-cli",
                reason="开始执行已规划动作，按真实运行进展续租 worker。",
                lease_id=session.lease_id,
                lease_holder=session.lease_holder,
                lease_ttl_seconds=DEFAULT_SESSION_LEASE_TTL_SECONDS,
            ),
        )
    # Motion tolerance is an explicit property of the game surface, not of the
    # caller.  Treating every pointer action issued by an external Agent as a
    # dynamic scene made ordinary static menus collect the full motion window
    # (17 probes in a live hero-list example) and could even fail a successful
    # action on a narrowly drawn target box.  Known and newly explored static
    # screens therefore use the compact static gate unless the caller explicitly
    # declares ``--dynamic-scene``.
    task_id = None
    external_owner_context = all(
        os.environ.get(name)
        for name in (
            EXTERNAL_AGENT_INVOCATION_ID_ENV,
            EXTERNAL_AGENT_SESSION_ID_ENV,
            EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV,
        )
    )
    if external_owner_context:
        external = _external_ledger(context).get_session(session.id)
        if external is None or external.environment_id != environment.id:
            raise click.ClickException("当前外部 Agent invocation 没有匹配的 canonical Session。")
        candidates = [
            candidate
            for candidate in session.active_task_ids
            if candidate in external.task_ids
            and (task := context.player().get_task(environment.id, candidate)) is not None
            and task.status == "active"
        ]
        if len(candidates) != 1:
            raise click.ClickException(
                f"外部 Agent 实机动作要求恰好一个 active canonical task；当前候选为 {candidates}。"
            )
        task_id = candidates[0]
    if session.remaining_action_budget <= 0:
        raise click.ClickException("Session 动作预算已经用尽。")
    expected_state_reference_artifact_id = None
    if fast_known_route and expected_semantic_state_id is not None and not dynamic_scene:
        dynamic_scene = _state_prototypes_require_motion_terminal(
            context,
            environment_id=environment_id,
            state_id=expected_semantic_state_id,
        )
    if fast_known_route and expected_semantic_state_id is not None:
        if not dynamic_scene:
            expected_state_reference_artifact_id = next(
                (
                    artifact.id
                    for artifact in context.player().list_recent_state_screenshot_prototypes(
                        environment_id,
                        expected_semantic_state_id,
                        limit=8,
                    )
                    if Path(artifact.path).is_file()
                ),
                None,
            )
    request = LiveStepRequestV1(
        target_id=run.target_id,
        environment_id=environment.id,
        session_id=session.id,
        task_id=task_id,
        initial_evidence=reference,
        viewport_width=run.viewport_width,
        viewport_height=run.viewport_height,
        game_id=run.game_id,
        build_scope_id=run.build_scope_id,
        action=action,
        target_name=target_name,
        target_bounds=target_bounds,
        account_action_intent=AccountActionIntentV1(
            id=f"intent.cli.{uuid.uuid4().hex}",
            category="native_game_automation",
            summary=expectation_summary,
            game_internal=True,
        ),
        expectation=LiveStepExpectationV1(
            summary=expectation_summary,
            kind="visual_change" if expect_change else "visual_no_change",
            min_visual_distance=min_visual_distance,
            stop_conditions=[
                "预期画面变化未成立。",
                "出现真实支付、外部身份资料、登录冲突或语义不明选择。",
                "单动作运行达到 60 秒。",
            ],
        ),
        actor="ai-player-cli",
        holder=f"ai-player-cli:{session.id}",
        lease_ttl_seconds=300 if fast_known_route else 120,
        max_runtime_seconds=60,
        settle_threshold=0.08 if fast_known_route else 0.01,
        required_consecutive=1 if fast_known_route else 2,
        settle_timeout_seconds=2.0 if fast_known_route else 4.0,
        sample_interval_seconds=0.15 if fast_known_route else 0.25,
        capture_profile="compact_static",
        source_semantic_state_id=source_semantic_state_id,
        source_state_observation_id=source_state_observation_id,
        expected_semantic_state_id=expected_semantic_state_id,
        expected_state_reference_artifact_id=expected_state_reference_artifact_id,
        skill_replay_version_id=skill_replay_version_id,
        locator_resolution=locator_resolution,
        before_surface_profile=(surface_profile_pair[0] if surface_profile_pair else None),
        after_surface_profile=(surface_profile_pair[1] if surface_profile_pair else None),
        defer_semantic_sedimentation=bool(context.runtime.get("defer_known_route_terminal_ingest")),
        dynamic_scene_profile=(
            EvidenceDynamicSceneProfile(
                max_inlier_frame_distance=0.12,
                analysis_window_frames=1 if fast_known_route else 5,
                required_inlier_ratio=1.0 if fast_known_route else 0.80,
            )
            if dynamic_scene
            else None
        ),
    )
    try:
        result = run_live_step(
            request,
            root=context.facility().store.root,
            route_runtime=route_runtime,
        )
    except Exception as exc:  # noqa: BLE001 - CLI translates the guarded execution boundary
        raise click.ClickException(str(exc)) from exc
    current = control.get_session(environment.id, session.id)
    if current is None or current.state != "running":
        raise click.ClickException("动作结束后 canonical Session 不再处于 running 状态。")
    reported_remaining = result.get("session_remaining_actions")
    if reported_remaining is not None:
        reported_remaining = int(reported_remaining)
        if current.remaining_action_budget != reported_remaining:
            raise click.ClickException("动作执行器回执的剩余预算与 canonical Session 不一致。")
        remaining_action_budget = reported_remaining
    else:
        # Backward-compatible boundary for older/injected executors that do not
        # reserve through DeviceGateway. Current live_step always reports the
        # already-reserved value, so a real action is never decremented twice.
        remaining_action_budget = max(0, current.remaining_action_budget - 1)
    terminal_reference = EvidenceReferenceV1(
        environment_id=environment.id,
        artifact_ids=[
            str(value)
            for key in ("before_artifact_id", "after_artifact_id", "video_artifact_id")
            if (value := result.get(key))
        ],
        evidence_run_ids=[str(result["evidence_run_id"])],
        evidence_step_ids=[str(result["evidence_step_id"])],
        trace_run_ids=[str(result["action_run_id"])] if result.get("action_run_id") else [],
        note="CLI 守卫动作的 Before、Action、After 终态证据。",
    )
    checkpoint = control.checkpoint(
        current.id,
        AIPlayerSessionCheckpointCommand(
            command_id=f"{current.id}.act.{current.version}.{uuid.uuid4().hex[:12]}",
            environment_id=current.environment_id,
            expected_version=current.version,
            actor="ai-player-cli",
            reason=(
                f"{_ACTION_EXPECTATION_MET_REASON_PREFIX}{expectation_summary}"
                if result["ok"]
                else f"{_ACTION_EXPECTATION_MISSED_REASON_PREFIX}{expectation_summary}"
            ),
            remaining_action_budget=remaining_action_budget,
            remaining_token_budget=current.remaining_token_budget,
            remaining_time_seconds=current.remaining_time_seconds,
            active_task_ids=current.active_task_ids,
            last_capsule_id=current.last_capsule_id,
            last_evidence_refs=[terminal_reference],
        ),
    )
    semantic_assignment = None
    memory_status = "handled_by_skill_runtime" if skill_replay_version_id else "not_applicable"
    candidate_version_ids: list[str] = []
    memory_error = None
    if result["ok"] and skill_replay_version_id is None:
        try:
            semantic_assignment = _ingest_and_resolve_evidence_state(
                context,
                environment_id=environment.id,
                evidence_refs=[terminal_reference],
            )
            discovery = crystallize_repeated_atomic_skill_candidates(
                context.player(),
                environment.id,
                current_state_id=(
                    semantic_assignment.state_id if semantic_assignment is not None else None
                ),
                limit=4,
            )
            candidate_version_ids = list(discovery.candidate_version_ids)
            memory_status = "persisted"
        except Exception as exc:  # noqa: BLE001 - action evidence remains canonical
            memory_status = "repair_required"
            memory_error = f"{type(exc).__name__}: {exc}"
    return {
        **result,
        "canonical_session": checkpoint,
        "semantic_state_id": (
            semantic_assignment.state_id if semantic_assignment is not None else None
        ),
        "memory_status": memory_status,
        "candidate_skill_version_ids": candidate_version_ids,
        "memory_error": memory_error,
    }


@click.group("game")
def cmd_game() -> None:
    """游戏观察与 AI 玩家设施。"""


@cmd_game.group("player")
@click.option(
    "--root",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Game Observatory 数据根；默认使用仓库 canonical root。",
)
@click.option("--json", "as_json", is_flag=True, help="输出稳定 UTF-8 JSON。")
@click.option(
    "--agent-brief",
    is_flag=True,
    help="仅供持续外部 Agent：动作与定位输出紧凑回执，完整 JSON 另存并锁定 hash。",
)
@click.pass_context
def cmd_player(
    ctx: click.Context,
    root: Path | None,
    as_json: bool,
    agent_brief: bool,
) -> None:
    """本地 AI 玩家统一入口。

    首次使用：doctor → environment use → context export → session start。
    持续探索：explore plan → explore run → evidence step → session checkpoint。
    所有设备写操作统一执行目标、来源状态、账号政策、预算、预期变化和证据守卫；
    真实货币支付与外部个人身份资料始终不在自主权限内。
    """

    external_invocation = bool(os.environ.get(EXTERNAL_AGENT_INVOCATION_ID_ENV))
    ctx.obj = _PlayerCLIContext(
        root=root,
        as_json=as_json,
        agent_brief=agent_brief or external_invocation,
    )


@cmd_player.command("doctor")
@click.pass_obj
def player_doctor(context: _PlayerCLIContext) -> None:
    """检查合同、数据库、外部 Agent CLI、环境和设备发现链。"""

    repository_root = _repository_root()
    checks: list[dict[str, Any]] = []
    try:
        contract_paths = check_external_agent_contracts(repository_root)
        checks.append(
            {
                "id": "facility-contract",
                "ok": True,
                "detail": [str(path) for path in contract_paths],
            }
        )
    except RuntimeError as exc:
        checks.append({"id": "facility-contract", "ok": False, "detail": str(exc)})
    facility = context.facility()
    player = AIPlayerStore(facility.store)
    checks.extend(
        [
            {
                "id": "canonical-database",
                "ok": facility.store.db_path.is_file(),
                "detail": str(facility.store.db_path),
            },
            {
                "id": "ai-player-schema",
                "ok": player.schema_version > 0,
                "detail": player.schema_version,
            },
            {
                "id": "codex-cli",
                "ok": shutil.which("codex") is not None,
                "detail": shutil.which("codex"),
            },
            {
                "id": "claude-code-cli",
                "ok": shutil.which("claude") is not None,
                "detail": shutil.which("claude"),
            },
        ]
    )
    try:
        targets = facility.discover_targets(refresh=False)
        checks.append({"id": "device-discovery", "ok": True, "detail": len(targets)})
    except Exception as exc:  # noqa: BLE001 - doctor must return every independent check
        checks.append({"id": "device-discovery", "ok": False, "detail": str(exc)})
        targets = []
    passed = sum(bool(item["ok"]) for item in checks)
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.doctor.v1",
            "passed": passed,
            "total": len(checks),
            "checks": checks,
            "environment_count": len(player.list_environments()),
            "target_count": len(targets),
        },
        summary=f"AI 玩家设施诊断：{passed}/{len(checks)} 通过",
    )
    if passed != len(checks):
        raise click.exceptions.Exit(1)


@cmd_player.group("acceptance")
def player_acceptance() -> None:
    """Verify a frozen P-13 receipt set and build the final package."""


@player_acceptance.command("run")
@click.option(
    "--request",
    "request_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--workspace-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--output-root",
    type=click.Path(path_type=Path, file_okay=False),
    required=True,
)
@click.option(
    "--trust-policy",
    "trust_policy_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
    help="Operator-selected acceptance trust policy; this file is the external trust root.",
)
@click.option(
    "--trust-policy-sha256",
    required=True,
    help="Operator-pinned SHA-256 of the trust-policy file.",
)
@click.option(
    "--write-failing-run",
    is_flag=True,
    help="Persist a FAIL package for diagnosis; the command still exits non-zero.",
)
@click.pass_obj
def player_acceptance_run(
    context: _PlayerCLIContext,
    request_path: Path,
    workspace_root: Path,
    output_root: Path,
    trust_policy_path: Path,
    trust_policy_sha256: str,
    write_failing_run: bool,
) -> None:
    """Fail closed unless all 11/13/10/12 gates and final reviews pass."""

    try:
        result = execute_acceptance_request(
            request_path,
            workspace_root=workspace_root,
            output_root=output_root,
            trust_policy_path=trust_policy_path,
            trust_policy_sha256=trust_policy_sha256,
            write_failing_run=write_failing_run,
        )
    except AcceptanceExecutionFailed as exc:
        _emit(
            context,
            {
                "schema": "game-observatory.ai-player.acceptance-cli-result.v1",
                "result": exc.result,
                "run_dir": str(exc.run_dir) if exc.run_dir is not None else None,
            },
            summary="AI-player acceptance: FAIL",
        )
        raise click.exceptions.Exit(1) from None
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.acceptance-cli-result.v1",
            "result": result,
            "run_dir": str(output_root.resolve() / result.run_id),
        },
        summary="AI-player acceptance: PASS",
    )


@cmd_player.group("context")
def player_context() -> None:
    """外部 Agent 一次加载的设施合同与焦点上下文。"""


@player_context.command("export")
@click.option("--environment", "environment_id", default=None, help="附带一个环境的焦点上下文。")
@click.option("--full", is_flag=True, help="附带完整控制台投影；默认只给紧凑摘要。")
@click.pass_obj
def player_context_export(
    context: _PlayerCLIContext,
    environment_id: str | None,
    full: bool,
) -> None:
    """导出同源设施合同；只在 Session 启动或合同变化时完整加载。"""

    contract = build_player_facility_contract()
    payload: dict[str, Any] = {
        "schema": "game-observatory.ai-player.external-agent-context.v1",
        "facility_contract": contract.model_dump(mode="json", by_alias=True),
    }
    if environment_id:
        projection = build_ai_player_console_projection(
            context.player(), environment_id=environment_id
        )
        payload["focus"] = (
            projection
            if full
            else {
                "selection": projection.get("selection"),
                "identity": projection.get("identity"),
                "current_state": projection.get("current_state"),
                "frontier": projection.get("frontier", [])[:10],
                "blockers": projection.get("blockers", []),
                "advisories": projection.get("advisories", []),
                "budget": projection.get("budget"),
            }
        )
    _emit(
        context,
        payload,
        summary=f"设施合同 {contract.facility_contract_sha256}",
    )


@cmd_player.group("environment")
def player_environment() -> None:
    """游戏、构建、账号、设备和区服环境。"""


@player_environment.command("register")
@click.option("--id", "environment_id", required=True, help="新的根环境稳定 ID。")
@click.option("--serial", required=True, help="在线 ADB serial；登记时只读捕获一次当前画面。")
@click.option("--game", "game_id", required=True)
@click.option("--game-alias", "game_id_aliases", multiple=True)
@click.option("--build", "build_scope_id", required=True)
@click.option("--build-alias", "build_scope_id_aliases", multiple=True)
@click.option("--account", "account_scope_id", required=True)
@click.option("--channel", required=True)
@click.option(
    "--device-alias",
    "device_scope_id_aliases",
    multiple=True,
    help="同一模拟器的其他稳定 Target ID，可重复。",
)
@click.option("--server", "server_scope_id", default=None)
@click.option("--world", "world_scope_id", default=None)
@click.option("--locale", default="zh-CN", show_default=True)
@click.option("--reason", required=True, help="为何这张终态截图足以建立当前根环境。")
@click.pass_obj
def player_environment_register(
    context: _PlayerCLIContext,
    environment_id: str,
    serial: str,
    game_id: str,
    game_id_aliases: tuple[str, ...],
    build_scope_id: str,
    build_scope_id_aliases: tuple[str, ...],
    account_scope_id: str,
    channel: str,
    device_scope_id_aliases: tuple[str, ...],
    server_scope_id: str | None,
    world_scope_id: str | None,
    locale: str,
    reason: str,
) -> None:
    """从在线模拟器当前画面建立首个不可变环境；已存在的同一环境幂等返回。"""

    facility = context.facility()
    player = AIPlayerStore(facility.store)
    target_id = f"device://adb/{serial.strip()}"
    existing = player.get_environment(environment_id)
    if existing is not None:
        expected_hash = _environment_identity_hash(
            game_id=game_id,
            game_id_aliases=game_id_aliases,
            build_scope_id=build_scope_id,
            build_scope_id_aliases=build_scope_id_aliases,
            account_scope_id=account_scope_id,
            channel=channel,
            device_scope_id=target_id,
            device_scope_id_aliases=device_scope_id_aliases,
            server_scope_id=server_scope_id,
            world_scope_id=world_scope_id,
            locale=locale,
            viewport_width=existing.viewport_width,
            viewport_height=existing.viewport_height,
        )
        if existing.identity_hash != expected_hash:
            raise click.ClickException(f"环境已经存在且身份参数不同，不能覆盖: {environment_id}")
        selection = player.select_environment_lineage(existing.id)
        _emit(
            context,
            {
                "schema": "game-observatory.ai-player.environment-registration-result.v1",
                "inserted_environment_count": 0,
                "environment": existing,
                "selection": selection,
                "capture": None,
                "persistence_reopen_verified": True,
                "next_command": f"omni game player environment use {existing.id}",
            },
            summary=f"根环境已存在并通过身份核对：{existing.id}",
        )
        return

    try:
        captured = facility.capture_device(serial.strip())
    except Exception as exc:  # noqa: BLE001 - normalize adapter failures for CLI users
        raise click.ClickException(str(exc)) from exc
    observation = captured["observation"]
    observed_target_id = str(observation["target_id"])
    if observed_target_id != target_id:
        raise click.ClickException(
            f"ADB 目标身份不一致: 预期 {target_id}，实际 {observed_target_id}"
        )
    source_frame = ArtifactRef.model_validate(observation["frame"])
    viewport_width, viewport_height = _png_dimensions(source_frame)
    identity_hash = _environment_identity_hash(
        game_id=game_id,
        game_id_aliases=game_id_aliases,
        build_scope_id=build_scope_id,
        build_scope_id_aliases=build_scope_id_aliases,
        account_scope_id=account_scope_id,
        channel=channel,
        device_scope_id=target_id,
        device_scope_id_aliases=device_scope_id_aliases,
        server_scope_id=server_scope_id,
        world_scope_id=world_scope_id,
        locale=locale,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )
    duplicate = next(
        (item for item in player.list_environments() if item.identity_hash == identity_hash),
        None,
    )
    if duplicate is not None:
        raise click.ClickException(f"同一环境身份已经登记为 {duplicate.id}，请使用已有环境。")
    binding_suffix = hashlib.sha256(
        f"{environment_id}:{source_frame.id}".encode("utf-8")
    ).hexdigest()[:16]
    bound_frame = source_frame.model_copy(
        update={
            "id": f"art.environment.{binding_suffix}",
            "metadata": {
                **source_frame.metadata,
                "environment_id": environment_id,
                "source_artifact_id": source_frame.id,
                "target_id": target_id,
                "game_id": game_id,
                "build_scope_id": build_scope_id,
                "account_scope_id": account_scope_id,
                "channel": channel,
                "environment_registration_reason": reason,
            },
        }
    )
    facility.store.save_artifact(bound_frame)
    environment = EnvironmentScopeV1(
        id=environment_id,
        game_id=game_id,
        game_id_aliases=list(game_id_aliases),
        build_scope_id=build_scope_id,
        build_scope_id_aliases=list(build_scope_id_aliases),
        account_scope_id=account_scope_id,
        channel=channel,
        device_scope_id=target_id,
        device_scope_id_aliases=list(device_scope_id_aliases),
        server_scope_id=server_scope_id,
        world_scope_id=world_scope_id,
        locale=locale,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        identity_hash=identity_hash,
        evidence_refs=[
            EvidenceReferenceV1(
                environment_id=environment_id,
                artifact_ids=[bound_frame.id],
                note=reason,
            )
        ],
        created_at=str(observation["captured_at"]),
    )
    try:
        player.put_environment(environment)
    except Exception as exc:  # noqa: BLE001 - preserve one CLI error boundary
        raise click.ClickException(str(exc)) from exc
    reopened = AIPlayerStore(GameObservatory(facility.store.root).store)
    selection = reopened.select_environment_lineage(environment.id)
    persistence_verified = reopened.get_environment(environment.id) == environment
    if not persistence_verified:
        raise click.ClickException("根环境写入后重开校验失败。")
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.environment-registration-result.v1",
            "inserted_environment_count": 1,
            "environment": environment,
            "selection": selection,
            "capture": {
                "target_id": target_id,
                "source_artifact_id": source_frame.id,
                "identity_artifact_id": bound_frame.id,
                "trace_run_id": source_frame.run_id,
            },
            "persistence_reopen_verified": persistence_verified,
            "next_command": f"omni game player environment use {environment.id}",
        },
        summary=f"已登记根环境：{environment.id}",
    )


@player_environment.command("promote")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
    help="EnvironmentPromotionV1 JSON。",
)
@click.option("--sha256", "expected_sha256", required=True, help="输入文件的预期 SHA-256。")
@click.pass_obj
def player_environment_promote(
    context: _PlayerCLIContext,
    input_path: Path,
    expected_sha256: str,
) -> None:
    """以 hash 锁定的终态证据把已知环境提升为更具体的账号或区服叶。"""

    facility = context.facility()
    try:
        result = ingest_environment_promotion_seed(
            facility.store.root,
            input_path,
            expected_store_root=facility.store.root,
            expected_seed_sha256=expected_sha256,
        )
    except Exception as exc:  # noqa: BLE001 - normalize typed validation for CLI users
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.environment-promotion-result.v1",
            "result": result,
            "next_command": (f"omni game player environment use {result.child_environment_id}"),
        },
        summary=f"环境已提升到当前叶：{result.child_environment_id}",
    )


@player_environment.command("list")
@click.option("--game", "game_id", default=None, help="按 game_id 筛选。")
@click.pass_obj
def player_environment_list(context: _PlayerCLIContext, game_id: str | None) -> None:
    """列出环境及其当前叶，不把不同账号或区服混在一起。"""

    player = context.player()
    environments = player.list_environments(game_id=game_id)
    current = {
        item.selected_environment_id for item in player.list_current_environment_selections()
    }
    items = [
        {
            **item.model_dump(mode="json", by_alias=True),
            "is_current_leaf": item.id in current,
        }
        for item in environments
    ]
    _emit(
        context,
        {"schema": "game-observatory.ai-player.environment-list.v1", "items": items},
        summary=f"AI 玩家环境：{len(items)} 个",
    )


@player_environment.command("show")
@click.argument("environment_id")
@click.pass_obj
def player_environment_show(context: _PlayerCLIContext, environment_id: str) -> None:
    """查看环境身份、继承路径和 canonical 来源。"""

    player = context.player()
    environment = _require_environment(player, environment_id)
    selection = player.select_environment_lineage(environment_id)
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.environment-detail.v1",
            "environment": environment,
            "selection": selection,
        },
        summary=f"环境：{environment.id}",
    )


@player_environment.command("use")
@click.argument("environment_id")
@click.pass_obj
def player_environment_use(context: _PlayerCLIContext, environment_id: str) -> None:
    """解析并返回唯一当前环境叶；命令不会创建隐藏的全局选择。"""

    player = context.player()
    selection = player.select_environment_lineage(environment_id)
    projection = build_ai_player_console_projection(
        player,
        environment_id=selection.selected_environment_id,
    )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.environment-use.v1",
            "selection": selection,
            "current_state": projection.get("current_state"),
            "frontier": projection.get("frontier", [])[:10],
            "next_command": (
                f"omni game player context export --environment {selection.selected_environment_id}"
            ),
        },
        summary=f"当前环境叶：{selection.selected_environment_id}",
    )


@cmd_player.group("device")
def player_device() -> None:
    """发现统一 CLI 可使用的本地模拟器与 ADB 目标。"""


@player_device.command("list")
@click.option("--refresh", is_flag=True, help="立即刷新 ADB 与模拟器发现记录。")
@click.pass_obj
def player_device_list(context: _PlayerCLIContext, refresh: bool) -> None:
    """列出目标 ID、在线状态、ADB serial 和适配器信息。"""

    try:
        targets = context.facility().discover_targets(refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - discovery adapters share one CLI boundary
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.device-list.v1",
            "refreshed": refresh,
            "items": targets,
            "next_command": "omni game player environment register --help",
        },
        summary=f"可用设备目标：{len(targets)} 个",
    )


@player_device.command("inspect")
@click.argument("serial")
@click.option("--package", default=None, help="同时核对安装状态、versionName 与 versionCode。")
@click.pass_obj
def player_device_inspect(
    context: _PlayerCLIContext,
    serial: str,
    package: str | None,
) -> None:
    """只读检查一个 ADB 目标的前台界面与可选游戏构建身份。"""

    try:
        inspected = context.facility().inspect_device(serial, package=package)
    except Exception as exc:  # noqa: BLE001 - normalize adapter failures for CLI users
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.device-inspection.v1",
            **inspected,
            "next_command": "omni game player environment register --help",
        },
        summary=f"设备检查完成：{serial}",
    )


@cmd_player.group("session")
def player_session() -> None:
    """持续 Session 的状态、恢复、心跳和检查点。"""


@player_session.command("start")
@click.option("--environment", "environment_id", required=True)
@click.option(
    "--provider",
    type=click.Choice(["codex-cli", "claude-code-cli"]),
    required=True,
)
@click.option("--model", "model_selector", required=True)
@click.option("--effort", type=click.Choice(["medium", "high"]), default="medium")
@click.option(
    "--permission",
    "permission_mode",
    type=click.Choice(["readonly", "workspace-write", "trusted-bypass"]),
    default="workspace-write",
    show_default=True,
)
@click.option(
    "--allow-trusted-bypass",
    is_flag=True,
    help="使用 trusted-bypass 时必须显式确认。",
)
@click.option("--phase", "phase_id", required=True)
@click.option("--objective", required=True)
@click.option("--prompt", "prompt_text", default=None, help="首轮具体任务。")
@click.option(
    "--prompt-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
)
@click.option(
    "--image",
    "image_paths",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    multiple=True,
    help="把当前证据图直接绑定到本轮模型输入，可重复；避免 Agent 内部 Read。",
)
@click.option("--session-id", default=None, help="省略时生成新 ID。")
@click.option("--previous-session", "previous_session_id", default=None)
@click.option(
    "--restart-reason",
    type=click.Choice(
        [
            "phase_complete",
            "provider_failover",
            "hard_reset",
            "environment_identity_change",
            "facility_contract_change",
            "unrecoverable_context_pollution",
            "benchmark_isolation",
        ]
    ),
    default=None,
)
@click.option("--task", "task_ids", multiple=True, help="绑定已有 FrontierTask ID。")
@click.option("--action-budget", type=click.IntRange(1), default=30, show_default=True)
@click.option("--token-budget", type=click.IntRange(1), default=None)
@click.option("--time-budget", type=click.FloatRange(min=1), default=3600, show_default=True)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=click.FloatRange(min=1, max=900),
    default=300,
    show_default=True,
)
@click.option(
    "--cwd",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=Path("."),
    show_default=True,
)
@click.pass_obj
def player_session_start(
    context: _PlayerCLIContext,
    environment_id: str,
    provider: str,
    model_selector: str,
    effort: str,
    permission_mode: str,
    allow_trusted_bypass: bool,
    phase_id: str,
    objective: str,
    prompt_text: str | None,
    prompt_file: Path | None,
    image_paths: tuple[Path, ...],
    session_id: str | None,
    previous_session_id: str | None,
    restart_reason: str | None,
    task_ids: tuple[str, ...],
    action_budget: int,
    token_budget: int | None,
    time_budget: float,
    timeout_seconds: float,
    cwd: Path,
) -> None:
    """建立 canonical 会话，并启动一个可多轮 resume 的原生外部 Session。"""

    if permission_mode == "trusted-bypass" and not allow_trusted_bypass:
        raise click.UsageError("trusted-bypass requires --allow-trusted-bypass")
    if prompt_text and prompt_file:
        raise click.UsageError("--prompt 与 --prompt-file 只能使用一个")
    if bool(previous_session_id) != bool(restart_reason):
        raise click.UsageError("--previous-session 与 --restart-reason 必须同时提供")
    task = prompt_file.read_text(encoding="utf-8") if prompt_file else prompt_text or objective
    player = context.player()
    _require_environment(player, environment_id)
    previous_external = None
    if previous_session_id is not None:
        previous_external = _external_ledger(context).get_session(previous_session_id)
        if previous_external is None:
            raise click.ClickException(f"上一代外部 Session 不存在: {previous_session_id}")
        if previous_external.environment_id != environment_id:
            raise click.ClickException("上一代外部 Session 属于另一个游戏环境。")
        provider_changed = previous_external.provider != provider
        if provider_changed and restart_reason != "provider_failover":
            raise click.ClickException(
                "同一代际链不能静默更换 provider；明确故障转移时使用 "
                "--restart-reason provider_failover。"
            )
        if restart_reason == "provider_failover" and not provider_changed:
            raise click.ClickException("provider_failover 必须切换到另一个 provider。")
        if restart_reason == "phase_complete" and previous_external.external_session_id:
            raise click.ClickException(
                "阶段完成且原生 provider Session 仍可续接；请使用 session rollover，"
                "避免新开原生 Session。"
            )
    control = AIPlayerSessionControl(player)
    resolved_session_id = session_id or f"ai-player-session.external.{uuid.uuid4().hex}"
    holder = f"external-agent:{provider}:{resolved_session_id}"
    created = control.create_session(
        CreateAIPlayerSessionCommand(
            command_id=f"{resolved_session_id}.create",
            requested_environment_id=environment_id,
            objective=objective,
            action_budget=action_budget,
            token_budget=token_budget,
            time_budget_seconds=time_budget,
            active_task_ids=list(task_ids),
            actor="ai-player-external-agent",
            reason="建立连续外部 Agent 游戏 Session。",
            session_id=resolved_session_id,
        )
    )
    running = control.start(
        created.id,
        AIPlayerSessionCommand(
            command_id=f"{resolved_session_id}.start",
            environment_id=created.environment_id,
            expected_version=created.version,
            actor="ai-player-external-agent",
            reason="启动连续外部 Agent 首轮。",
            lease_holder=holder,
            lease_ttl_seconds=max(120, int(timeout_seconds) + 60),
        ),
    )
    timestamp = utc_now()
    external = ExternalAgentContinuousSessionV1(
        id=running.id,
        provider=provider,
        model_selector=model_selector,
        requested_effort=effort,
        actual_effort="unreported",
        permission_mode=permission_mode,
        environment_id=running.environment_id,
        phase_id=phase_id,
        facility_contract_sha256=build_player_facility_contract().facility_contract_sha256,
        generation=(previous_external.generation + 1 if previous_external is not None else 1),
        previous_session_id=previous_session_id,
        restart_reason=restart_reason,
        task_ids=list(task_ids),
        started_at=timestamp,
        last_heartbeat_at=timestamp,
        updated_at=timestamp,
    )
    ledger = _external_ledger(context)
    runner = ContinuousExternalAgentRunner(
        ledger,
        timeout_cleanup_hook=ExternalAgentTimeoutResourceCleanup(
            context.facility().store.root,
            environment_id=running.environment_id,
            session_id=running.id,
        ),
    )
    prompt = _external_agent_start_prompt(
        context,
        session_id=running.id,
        environment_id=running.environment_id,
        objective=objective,
        task=task,
    )
    updated, invocation = asyncio.run(
        runner.start(
            external,
            prompt=prompt,
            image_paths=image_paths,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            no_progress_timeout_seconds=min(90.0, timeout_seconds),
        )
    )
    canonical_after = control.get_session(running.environment_id, running.id)
    if canonical_after is not None:
        updated = _sync_external_action_counts(ledger, updated, canonical_after)
    action_quality_samples = []
    action_quality_error = None
    try:
        action_quality_samples = persist_external_action_quality_samples(
            player,
            ledger,
            invocation,
        )
    except Exception as exc:  # noqa: BLE001 - surface canonical quality regressions
        action_quality_error = str(exc)
    gameplay_candidate_discovery = None
    gameplay_candidate_discovery_error = None
    try:
        report = discover_gameplay_candidates(
            player,
            running.environment_id,
            write_limit=4,
        )
        gameplay_candidate_discovery = {
            "scanned_edge_count": report.scanned_edge_count,
            "eligible_anchor_count": report.eligible_anchor_count,
            "candidate_version_ids": list(report.candidate_version_ids),
            "unchanged_candidate_ids": list(report.unchanged_candidate_ids),
            "review_locked_candidate_ids": list(report.review_locked_candidate_ids),
            "rejected_navigation_edge_count": report.rejected_navigation_edge_count,
            "rejected_incomplete_anchor_count": report.rejected_incomplete_anchor_count,
            "invalid_evidence_edge_count": report.invalid_evidence_edge_count,
        }
    except Exception as exc:  # noqa: BLE001 - discovery must not block live play
        gameplay_candidate_discovery_error = f"{type(exc).__name__}: {exc}"
    if invocation.status != "succeeded":
        current = control.get_session(running.environment_id, running.id)
        if current is not None and current.state == "running":
            control.pause(
                current.id,
                AIPlayerSessionCommand(
                    command_id=f"{current.id}.external-start-failed.{current.version}",
                    environment_id=current.environment_id,
                    expected_version=current.version,
                    actor="ai-player-external-agent",
                    reason=invocation.error or "外部 Agent 首轮失败。",
                ),
            )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.external-session-start-result.v1",
            "canonical_session": control.get_session(running.environment_id, running.id),
            "external_session": updated,
            "invocation": invocation,
            "action_quality_samples": action_quality_samples,
            "action_quality_error": action_quality_error,
            "gameplay_candidate_discovery": gameplay_candidate_discovery,
            "gameplay_candidate_discovery_error": gameplay_candidate_discovery_error,
        },
        summary=f"外部 Session {updated.id}: {invocation.status}",
    )
    if invocation.status != "succeeded":
        raise click.exceptions.Exit(1)
    if action_quality_error is not None:
        raise click.exceptions.Exit(1)


@player_session.command("rollover")
@click.argument("previous_session_id")
@click.option("--session-id", "successor_session_id", required=True)
@click.option("--phase", "phase_id", required=True)
@click.option("--objective", required=True)
@click.option("--task", "task_ids", multiple=True, required=True)
@click.option("--action-budget", type=click.IntRange(1), default=30, show_default=True)
@click.option("--token-budget", type=click.IntRange(1), default=None)
@click.option("--time-budget", type=click.FloatRange(min=1), default=3600, show_default=True)
@click.option(
    "--reason",
    type=click.Choice(["phase_complete", "facility_contract_change"]),
    required=True,
)
@click.pass_obj
def player_session_rollover(
    context: _PlayerCLIContext,
    previous_session_id: str,
    successor_session_id: str,
    phase_id: str,
    objective: str,
    task_ids: tuple[str, ...],
    action_budget: int,
    token_budget: int | None,
    time_budget: float,
    reason: str,
) -> None:
    """建立下一 canonical 代，并复用上一代原生 provider Session；不调用 provider。"""

    if successor_session_id == previous_session_id:
        raise click.UsageError("新旧 canonical Session ID 必须不同。")
    ledger = _external_ledger(context)
    previous_external = ledger.get_session(previous_session_id)
    if previous_external is None:
        raise click.ClickException(f"上一代外部 Session 不存在: {previous_session_id}")
    current_contract_hash = build_player_facility_contract().facility_contract_sha256
    if reason == "phase_complete":
        previous_external = _require_external_contract_current(context, previous_external)
    if not previous_external.external_session_id:
        raise click.ClickException("上一代没有可续接的原生 provider Session ID。")

    player = context.player()
    control = AIPlayerSessionControl(player)
    previous_canonical = control.get_session(
        previous_external.environment_id,
        previous_session_id,
    )
    if previous_canonical is None:
        raise click.ClickException("上一代 canonical Session 不存在。")
    if previous_canonical.environment_id != previous_external.environment_id:
        raise click.ClickException("上一代 canonical 与外部 Session 环境不一致。")
    for task_id in task_ids:
        if player.get_task(previous_external.environment_id, task_id) is None:
            raise click.ClickException(f"新阶段任务不存在于当前环境: {task_id}")

    successor_canonical = control.get_session(
        previous_external.environment_id,
        successor_session_id,
    )
    successor_external = ledger.get_session(successor_session_id)
    if previous_external.status not in {"active", "suspended"}:
        prior_successors = [
            item
            for item in ledger.list_sessions()
            if item.previous_session_id == previous_session_id and item.id != successor_session_id
        ]
        recoverable_partial = previous_external.status == "closed" and (
            successor_canonical is not None or successor_external is not None
        )
        resumable_closed_generation = (
            previous_external.status == "closed"
            and previous_canonical.state in {"safe_stopped", "completed"}
            and not prior_successors
        )
        if not recoverable_partial and not resumable_closed_generation:
            raise click.ClickException(
                f"上一代外部 Session 不能从 {previous_external.status} rollover。"
            )

    if successor_canonical is not None:
        canonical_matches = (
            successor_canonical.environment_id == previous_external.environment_id
            and successor_canonical.objective == objective
            and successor_canonical.action_budget == action_budget
            and successor_canonical.token_budget == token_budget
            and successor_canonical.time_budget_seconds == time_budget
            and successor_canonical.active_task_ids == list(task_ids)
        )
        if not canonical_matches or successor_canonical.state not in {"created", "running"}:
            raise click.ClickException("目标 canonical Session 已存在，但不属于本次 rollover。")
    else:
        try:
            successor_canonical = control.create_session(
                CreateAIPlayerSessionCommand(
                    command_id=f"{successor_session_id}.rollover.create",
                    requested_environment_id=previous_external.environment_id,
                    objective=objective,
                    action_budget=action_budget,
                    token_budget=token_budget,
                    time_budget_seconds=time_budget,
                    active_task_ids=list(task_ids),
                    actor="ai-player-external-agent",
                    reason=f"从 {previous_session_id} 建立连续原生 Session 的下一阶段。",
                    session_id=successor_session_id,
                )
            )
        except AIPlayerSessionError as exc:
            raise click.ClickException(exc.message) from exc

    if previous_canonical.state not in {"safe_stopped", "completed"}:
        try:
            previous_canonical = control.safe_stop(
                previous_session_id,
                AIPlayerSessionCommand(
                    command_id=f"{successor_session_id}.rollover.stop-previous",
                    environment_id=previous_external.environment_id,
                    expected_version=previous_canonical.version,
                    actor="ai-player-external-agent",
                    reason=f"{reason}: rollover to {successor_session_id}",
                ),
            )
        except AIPlayerSessionError as exc:
            raise click.ClickException(
                f"rollover 已建立待启动的新代，但旧 canonical 停止失败；可用同一命令恢复: "
                f"{exc.message}"
            ) from exc

    if successor_canonical.state == "created":
        try:
            successor_canonical = control.start(
                successor_session_id,
                AIPlayerSessionCommand(
                    command_id=f"{successor_session_id}.rollover.start",
                    environment_id=previous_external.environment_id,
                    expected_version=successor_canonical.version,
                    actor="ai-player-external-agent",
                    reason="启动复用原生 provider Session 的下一阶段。",
                    lease_holder=(
                        f"external-agent:{previous_external.provider}:{successor_session_id}"
                    ),
                    lease_ttl_seconds=DEFAULT_SESSION_LEASE_TTL_SECONDS,
                ),
            )
        except AIPlayerSessionError as exc:
            raise click.ClickException(
                f"旧代已安全停止，新代启动失败；可用同一命令恢复: {exc.message}"
            ) from exc

    previous_navigation_stack = player.get_navigation_stack(
        previous_external.environment_id,
        previous_session_id,
    )
    successor_navigation_stack = player.get_navigation_stack(
        previous_external.environment_id,
        successor_session_id,
    )
    if previous_navigation_stack is not None and previous_navigation_stack.frames:
        if successor_navigation_stack is None:
            for frame in previous_navigation_stack.frames:
                successor_navigation_stack = player.push_navigation_frame(
                    previous_external.environment_id,
                    successor_session_id,
                    frame,
                )
        elif (
            successor_navigation_stack.current_state_id
            != previous_navigation_stack.current_state_id
            or successor_navigation_stack.frames != previous_navigation_stack.frames
        ):
            raise click.ClickException(
                "目标 Session 已有不同的导航调用栈，拒绝覆盖当前设备上下文。"
            )

    timestamp = utc_now()
    if previous_external.status in {"active", "suspended"}:
        previous_external = ledger.update_session(
            previous_external.model_copy(
                update={
                    "version": previous_external.version + 1,
                    "status": "closed",
                    "last_heartbeat_at": timestamp,
                    "updated_at": timestamp,
                }
            ),
            expected_version=previous_external.version,
        )

    if successor_external is None:
        successor_external = ledger.create_session(
            ExternalAgentContinuousSessionV1(
                id=successor_session_id,
                provider=previous_external.provider,
                model_selector=previous_external.model_selector,
                resolved_model_id=previous_external.resolved_model_id,
                requested_effort=previous_external.requested_effort,
                actual_effort=previous_external.actual_effort,
                permission_mode=previous_external.permission_mode,
                external_session_id=previous_external.external_session_id,
                environment_id=previous_external.environment_id,
                phase_id=phase_id,
                facility_contract_sha256=(current_contract_hash),
                generation=previous_external.generation + 1,
                previous_session_id=previous_session_id,
                restart_reason=reason,
                status="active",
                task_ids=list(task_ids),
                started_at=timestamp,
                last_heartbeat_at=timestamp,
                updated_at=timestamp,
            )
        )
    else:
        external_matches = (
            successor_external.provider == previous_external.provider
            and successor_external.model_selector == previous_external.model_selector
            and successor_external.permission_mode == previous_external.permission_mode
            and successor_external.external_session_id == previous_external.external_session_id
            and successor_external.environment_id == previous_external.environment_id
            and successor_external.phase_id == phase_id
            and successor_external.generation == previous_external.generation + 1
            and successor_external.previous_session_id == previous_session_id
            and successor_external.restart_reason == reason
            and successor_external.facility_contract_sha256 == current_contract_hash
            and successor_external.task_ids == list(task_ids)
            and successor_external.invocation_count == 0
        )
        if not external_matches or successor_external.status != "active":
            raise click.ClickException("目标外部 Session 已存在，但不属于本次 rollover。")

    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.session-rollover-result.v1",
            "previous_canonical_session": previous_canonical,
            "previous_external_session": previous_external,
            "canonical_session": successor_canonical,
            "external_session": successor_external,
            "navigation_stack": successor_navigation_stack,
            "provider_invoked": False,
        },
        summary=(
            f"已 rollover 到 {successor_session_id}；后续 resume 复用原生 Session "
            f"{successor_external.external_session_id}"
        ),
    )


@player_session.command("resume")
@click.argument("session_id")
@click.option("--prompt", "prompt_text", default=None, help="本轮任务增量。")
@click.option(
    "--prompt-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
)
@click.option(
    "--image",
    "image_paths",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    multiple=True,
    help="把最新证据图直接绑定到同一原生 Session 的本轮输入，可重复。",
)
@click.option("--timeout", "timeout_seconds", type=click.FloatRange(min=1), default=900)
@click.option(
    "--cwd",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=Path("."),
    show_default=True,
)
@click.pass_obj
def player_session_resume(
    context: _PlayerCLIContext,
    session_id: str,
    prompt_text: str | None,
    prompt_file: Path | None,
    image_paths: tuple[Path, ...],
    timeout_seconds: float,
    cwd: Path,
) -> None:
    """续接原 provider Session；只加载本轮增量和恢复核对，不重读完整合同。"""

    if prompt_text and prompt_file:
        raise click.UsageError("--prompt 与 --prompt-file 只能使用一个")
    task = prompt_file.read_text(encoding="utf-8") if prompt_file else prompt_text
    if not task or not task.strip():
        raise click.UsageError("resume requires --prompt or --prompt-file")
    ledger = _external_ledger(context)
    external = ledger.get_session(session_id)
    if external is None:
        raise click.ClickException(f"外部 Agent Session 不存在: {session_id}")
    external = _require_external_contract_current(context, external)
    control = AIPlayerSessionControl(context.player())
    holder = f"external-agent:{external.provider}:{external.id}"
    canonical_current = _ensure_canonical_session_running(
        control,
        session_id=session_id,
        environment_id=external.environment_id,
        holder=holder,
        lease_ttl_seconds=max(120, int(timeout_seconds) + 60),
    )
    runner = ContinuousExternalAgentRunner(
        ledger,
        timeout_cleanup_hook=ExternalAgentTimeoutResourceCleanup(
            context.facility().store.root,
            environment_id=external.environment_id,
            session_id=external.id,
        ),
    )
    if external.restart_reason == "facility_contract_change" and external.invocation_count == 0:
        prompt = _external_agent_start_prompt(
            context,
            session_id=session_id,
            environment_id=external.environment_id,
            objective=canonical_current.objective,
            task=task.strip(),
        )
    else:
        prompt = (
            f"{_EXTERNAL_AGENT_HOST_CONTROL_BOUNDARY}"
            f"续接同一原生 AI 玩家历史。本轮唯一可写 canonical Session ID 是 {session_id}；"
            "所有带 --session 的命令必须使用该值，上一代 ID 禁止写入。当前增量任务和随附图片"
            "就是本轮焦点上下文；有明确的"
            " source EvidenceStep 时直接据此决策，禁止重复运行 state current、session status"
            "、完整 state map、无过滤 skill list 或帮助命令。目标界面已经明确时，第一条命令"
            "直接调用 navigate <目标>；它会在程序层查询状态图和技能图。navigate 在设备动作前"
            "返回无已知路径后，才观察当前界面并只探索缺失片段。只有增量任务没有给出预算或"
            "来源时，才可使用精确命令 "
            f"omni game player --json session status --environment {external.environment_id} "
            f"--id {session_id}。不要重读完整设施合同，不要盲目重放上轮动作。保持运行前预期、"
            "固定层回放后若终态界面与目标语义明确不符，直接使用回执的 after_preview 与 "
            "terminal_evidence_step_id；回执缺图时才另行观察。使用紧凑回执 "
            "executed_skills 中的精确版本调用 skill invalidate；禁止为取得版本号读取完整回执，"
            "不得停用未实际回放的技能。"
            "仅在前置条件满足且控件进入无关业务界面时停用。资源不足、并发占用、冷却、"
            "解锁条件、次数限制、确认或警告弹窗属于运行时前置失败；保留失败运行，禁止停用。"
            "运行后只核对动作回执中的 EvidenceStep、剩余预算与预期变化。对白推进或局部"
            "文字替换使用 min_visual_distance=0.01；场景或布局迁移使用 0.03，并以实际内容"
            "复核阈值判断。\n\n本轮任务：\n" + task.strip()
        )
    updated, invocation = asyncio.run(
        runner.resume(
            session_id,
            prompt=prompt,
            image_paths=image_paths,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            no_progress_timeout_seconds=min(90.0, timeout_seconds),
        )
    )
    canonical_after = control.get_session(external.environment_id, session_id)
    if canonical_after is not None:
        updated = _sync_external_action_counts(ledger, updated, canonical_after)
    state_ingest = None
    state_ingest_error = None
    try:
        state_ingest = ingest_external_invocation_state_evidence(
            context.facility().store.root,
            environment_id=external.environment_id,
            invocation=invocation,
        )
    except Exception as exc:  # noqa: BLE001 - surface state sedimentation regressions
        state_ingest_error = str(exc)
    action_quality_samples = []
    action_quality_error = None
    try:
        action_quality_samples = persist_external_action_quality_samples(
            context.player(),
            ledger,
            invocation,
        )
    except Exception as exc:  # noqa: BLE001 - surface canonical quality regressions
        action_quality_error = str(exc)
    gameplay_candidate_discovery = None
    gameplay_candidate_discovery_error = None
    try:
        report = discover_gameplay_candidates(
            context.player(),
            external.environment_id,
            write_limit=4,
        )
        gameplay_candidate_discovery = {
            "scanned_edge_count": report.scanned_edge_count,
            "eligible_anchor_count": report.eligible_anchor_count,
            "candidate_version_ids": list(report.candidate_version_ids),
            "unchanged_candidate_ids": list(report.unchanged_candidate_ids),
            "review_locked_candidate_ids": list(report.review_locked_candidate_ids),
            "rejected_navigation_edge_count": report.rejected_navigation_edge_count,
            "rejected_incomplete_anchor_count": report.rejected_incomplete_anchor_count,
            "invalid_evidence_edge_count": report.invalid_evidence_edge_count,
        }
    except Exception as exc:  # noqa: BLE001 - discovery must not block live play
        gameplay_candidate_discovery_error = f"{type(exc).__name__}: {exc}"
    if invocation.status != "succeeded":
        current = control.get_session(external.environment_id, session_id)
        if current is not None and current.state == "running":
            control.pause(
                current.id,
                AIPlayerSessionCommand(
                    command_id=f"{current.id}.external-turn-failed.{current.version}",
                    environment_id=current.environment_id,
                    expected_version=current.version,
                    actor="ai-player-external-agent",
                    reason=invocation.error or "外部 Agent 续接轮失败。",
                ),
            )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.external-session-resume-result.v1",
            "canonical_session": control.get_session(external.environment_id, session_id),
            "external_session": updated,
            "invocation": invocation,
            "state_ingest": state_ingest,
            "state_ingest_error": state_ingest_error,
            "action_quality_samples": action_quality_samples,
            "action_quality_error": action_quality_error,
            "gameplay_candidate_discovery": gameplay_candidate_discovery,
            "gameplay_candidate_discovery_error": gameplay_candidate_discovery_error,
        },
        summary=f"续接 {updated.id} 第 {invocation.sequence} 轮: {invocation.status}",
    )
    if invocation.status != "succeeded":
        raise click.exceptions.Exit(1)
    if state_ingest_error is not None:
        raise click.exceptions.Exit(1)
    if action_quality_error is not None:
        raise click.exceptions.Exit(1)


@player_session.command("status")
@click.option("--environment", "environment_id", required=True)
@click.option("--id", "session_id", default=None, help="指定 Session；省略时列出该环境的 Session。")
@click.option("--limit", default=100, show_default=True, type=click.IntRange(1, 500))
@click.pass_obj
def player_session_status(
    context: _PlayerCLIContext,
    environment_id: str,
    session_id: str | None,
    limit: int,
) -> None:
    """查看动作预算、worker lease、心跳、检查点和生命周期事件。"""

    control = AIPlayerSessionControl(context.player())
    ledger = _external_ledger(context)
    if session_id:
        session = control.get_session(environment_id, session_id)
        if session is None:
            raise click.ClickException(f"当前环境中没有这个 Session: {session_id}")
        payload = {
            "schema": "game-observatory.ai-player.session-detail.v1",
            "session": session,
            "events": control.list_events(environment_id, session_id),
            "external_session": ledger.get_session(session_id),
            "external_invocations": ledger.list_invocations(session_id),
            "external_heartbeat": ledger.read_heartbeat(session_id),
        }
        summary = f"Session {session.id}: {session.state}"
    else:
        sessions = control.list_sessions(environment_id, limit=limit)
        external_by_id = {
            item.id: item
            for item in ledger.list_sessions()
            if item.environment_id == environment_id
        }
        payload = {
            "schema": "game-observatory.ai-player.session-list.v1",
            "sessions": sessions,
            "external_sessions": [
                external_by_id[item.id] for item in sessions if item.id in external_by_id
            ],
        }
        summary = f"Session：{len(sessions)} 个"
    _emit(context, payload, summary=summary)


@player_session.command("heartbeat")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_session_heartbeat(
    context: _PlayerCLIContext,
    session_id: str,
    environment_id: str,
) -> None:
    """续租 canonical worker lease，并同步外部 Session 轻量心跳。"""

    control = AIPlayerSessionControl(context.player())
    current = _recover_stale_external_running_session(
        context,
        control,
        session_id=session_id,
        environment_id=environment_id,
    )
    if (
        current is None
        or current.state != "running"
        or not current.lease_id
        or not current.lease_holder
    ):
        raise click.ClickException("Session 不在可续租的 running 状态。")
    updated = control.heartbeat(
        session_id,
        AIPlayerSessionHeartbeatCommand(
            command_id=f"{session_id}.manual-heartbeat.{current.version}",
            environment_id=environment_id,
            expected_version=current.version,
            actor="ai-player-cli",
            reason="操作者请求外部 Agent Session 心跳。",
            lease_id=current.lease_id,
            lease_holder=current.lease_holder,
            lease_ttl_seconds=DEFAULT_SESSION_LEASE_TTL_SECONDS,
        ),
    )
    ledger = _external_ledger(context)
    external = ledger.get_session(session_id)
    if external is not None:
        ledger.write_heartbeat(
            session_id,
            sequence=external.invocation_count,
            timestamp=updated.last_heartbeat_at,
        )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.session-heartbeat-result.v1",
            "session": updated,
            "external_heartbeat": ledger.read_heartbeat(session_id),
        },
        summary=f"Session {session_id} 心跳已续租",
    )


@player_session.command("checkpoint")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--remaining-actions", type=click.IntRange(0), default=None)
@click.option("--remaining-tokens", type=click.IntRange(0), default=None)
@click.option("--remaining-seconds", type=click.FloatRange(min=0), default=None)
@click.option(
    "--budget-correction-step",
    default=None,
    help="仅在修复已证实的重复扣账时，绑定对应 EvidenceStep；不会重放动作。",
)
@click.option("--reason", required=True)
@click.pass_obj
def player_session_checkpoint(
    context: _PlayerCLIContext,
    session_id: str,
    environment_id: str,
    remaining_actions: int | None,
    remaining_tokens: int | None,
    remaining_seconds: float | None,
    budget_correction_step: str | None,
    reason: str,
) -> None:
    """保存任务、预算、最近证据和断点；不触发设备动作。"""

    control = AIPlayerSessionControl(context.player())
    current = _recover_stale_external_running_session(
        context,
        control,
        session_id=session_id,
        environment_id=environment_id,
    )
    token_value = (
        remaining_tokens if remaining_tokens is not None else current.remaining_token_budget
    )
    action_value = (
        remaining_actions if remaining_actions is not None else current.remaining_action_budget
    )
    try:
        if action_value > current.remaining_action_budget:
            if not budget_correction_step:
                raise click.ClickException(
                    "增加动作预算只允许用于有 EvidenceStep 的重复扣账修正；请提供 --budget-correction-step。"
                )
            updated = control.correct_action_budget(
                session_id,
                AIPlayerSessionBudgetCorrectionCommand(
                    command_id=f"{session_id}.budget-correction.{current.version}",
                    environment_id=environment_id,
                    expected_version=current.version,
                    actor="ai-player-cli",
                    reason=reason,
                    expected_remaining_action_budget=current.remaining_action_budget,
                    corrected_remaining_action_budget=action_value,
                    evidence_refs=[
                        EvidenceReferenceV1(
                            environment_id=environment_id,
                            evidence_step_ids=[budget_correction_step],
                            note="动作预算重复扣账的终态证据。",
                        )
                    ],
                ),
            )
        else:
            if budget_correction_step:
                raise click.ClickException(
                    "--budget-correction-step 只用于上调被重复扣除的动作预算。"
                )
            updated = control.checkpoint(
                session_id,
                AIPlayerSessionCheckpointCommand(
                    command_id=f"{session_id}.manual-checkpoint.{current.version}",
                    environment_id=environment_id,
                    expected_version=current.version,
                    actor="ai-player-cli",
                    reason=reason,
                    remaining_action_budget=action_value,
                    remaining_token_budget=token_value,
                    remaining_time_seconds=(
                        remaining_seconds
                        if remaining_seconds is not None
                        else current.remaining_time_seconds
                    ),
                    active_task_ids=current.active_task_ids,
                    last_capsule_id=current.last_capsule_id,
                    last_evidence_refs=current.last_evidence_refs,
                ),
            )
    except AIPlayerSessionError as exc:
        raise click.ClickException(exc.message) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.session-checkpoint-result.v1",
            "session": updated,
        },
        summary=f"Session {session_id} 已写检查点 v{updated.version}",
    )


@player_session.command("stop")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--reason", required=True)
@click.pass_obj
def player_session_stop(
    context: _PlayerCLIContext,
    session_id: str,
    environment_id: str,
    reason: str,
) -> None:
    """安全停止设备计划，关闭 canonical lease，并保留全部恢复与事件材料。"""

    control = AIPlayerSessionControl(context.player())
    current = control.get_session(environment_id, session_id)
    if current is None:
        raise click.ClickException(f"Session 不存在: {session_id}")
    stopped = current
    if current.state not in {"safe_stopped", "completed"}:
        stopped = control.safe_stop(
            session_id,
            AIPlayerSessionCommand(
                command_id=f"{session_id}.manual-stop.{current.version}",
                environment_id=environment_id,
                expected_version=current.version,
                actor="ai-player-cli",
                reason=reason,
            ),
        )
    ledger = _external_ledger(context)
    external = ledger.get_session(session_id)
    if external is not None and external.status in {"active", "suspended"}:
        timestamp = utc_now()
        payload = external.model_dump(mode="json", by_alias=True)
        payload.update(
            version=external.version + 1,
            status="closed",
            last_heartbeat_at=timestamp,
            updated_at=timestamp,
        )
        external = ledger.update_session(
            ExternalAgentContinuousSessionV1.model_validate(payload),
            expected_version=external.version,
        )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.session-stop-result.v1",
            "canonical_session": stopped,
            "external_session": external,
        },
        summary=f"Session {session_id} 已安全停止",
    )


def _capture_environment_observation(
    context: _PlayerCLIContext,
    environment_id: str,
    serial: str | None,
) -> dict[str, Any]:
    """Capture one authoritative current frame without emitting another CLI document."""

    environment = _require_environment(context.player(), environment_id)
    resolved_serial = _resolve_environment_serial(
        context,
        environment_id=environment_id,
        serial=serial,
    )
    try:
        captured = context.facility().capture_device(resolved_serial)
    except Exception as exc:  # noqa: BLE001 - normalize adapter failures for CLI users
        raise click.ClickException(str(exc)) from exc
    observation = captured["observation"]
    source_frame = ArtifactRef.model_validate(observation["frame"])
    viewport_width, viewport_height = _png_dimensions(source_frame)
    expected_dimensions = (
        environment.viewport_width,
        environment.viewport_height,
    )
    # MuMu/UIAutomator can briefly restore the Android user rotation after an
    # action, yielding one portrait screenshot while the foreground game is
    # already returning to landscape.  Retry only this exact quarter-turn
    # shape once; every other identity mismatch remains a hard failure.
    if expected_dimensions[0] != expected_dimensions[1] and (
        viewport_width,
        viewport_height,
    ) == tuple(reversed(expected_dimensions)):
        time.sleep(0.5)
        try:
            captured = context.facility().capture_device(resolved_serial)
        except Exception as exc:  # noqa: BLE001 - normalize adapter failures
            raise click.ClickException(str(exc)) from exc
        observation = captured["observation"]
        source_frame = ArtifactRef.model_validate(observation["frame"])
        viewport_width, viewport_height = _png_dimensions(source_frame)
    if (viewport_width, viewport_height) != expected_dimensions:
        raise click.ClickException(
            "当前设备分辨率与环境身份不一致: "
            f"{viewport_width}x{viewport_height} != "
            f"{environment.viewport_width}x{environment.viewport_height}"
        )
    binding_suffix = hashlib.sha256(
        f"{environment.id}:{source_frame.id}:observation".encode("utf-8")
    ).hexdigest()[:16]
    evidence_run_id = f"evidence.run.observe.{uuid.uuid4().hex}"
    evidence_step_id = f"evidence.step.observe.{uuid.uuid4().hex}"
    bound_frame = source_frame.model_copy(
        update={
            "id": f"art.environment-observation.{binding_suffix}",
            "metadata": {
                **source_frame.metadata,
                "environment_id": environment.id,
                "source_artifact_id": source_frame.id,
                "semantic_state_eligible": True,
                "observation_only": True,
                "evidence_run_id": evidence_run_id,
                "evidence_step_id": evidence_step_id,
                "evidence_role": "observation",
            },
        }
    )
    facility = context.facility()
    facility.store.save_artifact(bound_frame)
    preview, preview_mapping = _agent_preview(
        facility,
        source_frame=source_frame,
        environment_id=environment.id,
        binding_suffix=binding_suffix,
        source_width=viewport_width,
        source_height=viewport_height,
    )
    bound_ui: ArtifactRef | None = None
    if observation.get("ui_tree"):
        source_ui = ArtifactRef.model_validate(observation["ui_tree"])
        bound_ui = source_ui.model_copy(
            update={
                "id": f"art.environment-observation-ui.{binding_suffix}",
                "metadata": {
                    **source_ui.metadata,
                    "environment_id": environment.id,
                    "source_artifact_id": source_ui.id,
                    "observation_only": True,
                    "evidence_run_id": evidence_run_id,
                    "evidence_step_id": evidence_step_id,
                    "evidence_role": "observation_ui_tree",
                },
            }
        )
        facility.store.save_artifact(bound_ui)
    stamp = str(observation["captured_at"])
    artifact_ids = [bound_frame.id, preview.id] + ([bound_ui.id] if bound_ui else [])
    trace_run_ids = [source_frame.run_id] if source_frame.run_id else []
    evidence_step = EvidenceStep(
        id=evidence_step_id,
        evidence_run_id=evidence_run_id,
        step_index=1,
        status="passed",
        started_at=stamp,
        ended_at=stamp,
        before_frame_id=bound_frame.id,
        before_ui_tree_id=bound_ui.id if bound_ui else None,
        action=NormalizedAction(type="wait", seconds=0),
        target_name="当前设备只读观察锚点",
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        after_frame_id=bound_frame.id,
        after_ui_tree_id=bound_ui.id if bound_ui else None,
        artifact_ids=artifact_ids,
        observation_run_ids=trace_run_ids,
        stability=EvidenceStability(
            required_consecutive=1,
            observed_consecutive=1,
            final_distance=0,
            sample_distances=[0],
            sampled_frames=1,
            settled=True,
        ),
        quality_advisories=["这是只读观察锚点，不代表一次游戏内行为。"],
        metadata={"capture_profile": "compact_static", "observation_only": True},
    )
    evidence_run = EvidenceRun(
        id=evidence_run_id,
        target_id=str(observation["target_id"]),
        adapter="adb",
        status="passed",
        game_id=environment.game_id,
        build_scope_id=environment.build_scope_id,
        scope_id=environment.id,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        orientation=(
            "portrait"
            if viewport_height > viewport_width
            else "landscape"
            if viewport_width > viewport_height
            else "square"
        ),
        environment={
            "environment_id": environment.id,
            "game_id": environment.game_id,
            "build_scope_id": environment.build_scope_id,
            "account_scope_id": environment.account_scope_id,
            "device_scope_id": environment.device_scope_id,
            "channel": environment.channel,
        },
        started_at=stamp,
        ended_at=stamp,
        step_ids=[evidence_step.id],
        artifact_ids=artifact_ids,
        observation_run_ids=trace_run_ids,
    )
    facility.store.save_evidence_run(evidence_run)
    facility.store.save_evidence_step(evidence_step)
    return {
        "schema": "game-observatory.ai-player.observation-capture.v1",
        "environment_id": environment_id,
        "serial": resolved_serial,
        "target": captured["target"],
        "observation": {
            **observation,
            "frame": bound_frame,
            "ui_tree": bound_ui,
        },
        "agent_preview": {
            "artifact": preview,
            "coordinate_mapping": preview_mapping,
            "instruction": (
                "识别预览图后，把预览坐标分别乘 source_pixel_scale_x/y，"
                "得到 act 命令使用的原图坐标。"
            ),
        },
        "evidence_run": evidence_run,
        "evidence_step": evidence_step,
        "next_command": f"omni game player evidence step {evidence_step.id}",
    }


@cmd_player.group("observe")
def player_observe() -> None:
    """只读捕获和检查当前画面，不触发游戏动作。"""


@player_observe.command("capture")
@click.option("--environment", "environment_id", required=True)
@click.option("--serial", default=None, help="可选 ADB serial；默认从环境设备身份解析。")
@click.option(
    "--focus",
    is_flag=True,
    help="仅在 stdout 返回小预览路径、坐标映射和 EvidenceStep；完整观察仍会保存。",
)
@click.pass_obj
def player_observe_capture(
    context: _PlayerCLIContext,
    environment_id: str,
    serial: str | None,
    focus: bool,
) -> None:
    """保存当前画面，并建立可供第一次动作引用的只读 EvidenceStep 锚点。"""

    payload = _capture_environment_observation(context, environment_id, serial)
    evidence_step = EvidenceStep.model_validate(payload["evidence_step"])
    if focus:
        preview = ArtifactRef.model_validate(payload["agent_preview"]["artifact"])
        coordinate_mapping = payload["agent_preview"]["coordinate_mapping"]
        payload = {
            "schema": "game-observatory.ai-player.observation-focus.v1",
            "evidence_step_id": evidence_step.id,
            "agent_preview": {
                "path": preview.path,
                "sha256": preview.sha256,
                "size_bytes": Path(preview.path).stat().st_size,
            },
            "coordinate_mapping": coordinate_mapping,
            "pointer_contract": _preview_pointer_contract(
                coordinate_mapping,
                evidence_step_id=evidence_step.id,
            ),
            "captured_at": payload["observation"]["captured_at"],
        }
    _emit(
        context,
        payload,
        summary=f"已捕获当前画面并建立观察锚点：{evidence_step.id}",
    )


@player_observe.command("inspect")
@click.option("--artifact", "artifact_id", default=None, help="截图或 UI 树 artifact ID。")
@click.option("--run", "run_id", default=None, help="observe trace run ID。")
@click.pass_obj
def player_observe_inspect(
    context: _PlayerCLIContext,
    artifact_id: str | None,
    run_id: str | None,
) -> None:
    """读取一次观察的截图、UI 树、hash、媒体信息和采集事件。"""

    if bool(artifact_id) == bool(run_id):
        raise click.UsageError("--artifact 与 --run 必须且只能提供一个")
    store = context.facility().store
    selected_artifact = store.get_artifact(artifact_id) if artifact_id else None
    if artifact_id and selected_artifact is None:
        raise click.ClickException(f"没有这个 artifact: {artifact_id}")
    resolved_run_id = run_id or str(selected_artifact.run_id or "")
    if not resolved_run_id:
        raise click.ClickException("artifact 没有绑定 trace run。")
    run = store.get_run(resolved_run_id)
    if run is None:
        raise click.ClickException(f"没有这个 observe trace run: {resolved_run_id}")
    artifacts = [
        artifact for item in run.artifact_ids if (artifact := store.get_artifact(item)) is not None
    ]
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.observation-inspection.v1",
            "run": run,
            "artifacts": artifacts,
            "events": store.list_trace_events(run.id),
        },
        summary=f"观察包 {run.id}：{len(artifacts)} 个 artifact",
    )


def _canonical_locator_source(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    source_step_id: str | None,
    artifact_id: str | None,
) -> tuple[Any, Any, Any, ArtifactRef]:
    if bool(source_step_id) == bool(artifact_id):
        raise click.UsageError("--source-step and --artifact require exactly one value")
    if source_step_id:
        environment, run, step, _reference = _evidence_reference_from_step(
            context,
            environment_id=environment_id,
            step_id=source_step_id,
            require_semantic_source=True,
        )
        source = context.facility().store.get_artifact(str(step.after_frame_id))
        assert source is not None
        return environment, run, step, source

    store = context.facility().store
    source = store.get_artifact(str(artifact_id))
    if source is None or source.metadata.get("environment_id") != environment_id:
        raise click.ClickException("artifact is absent or belongs to another environment")
    if source.metadata.get("semantic_state_eligible") is not True:
        raise click.ClickException("artifact is not a canonical semantic action source")
    bound_step_id = str(source.metadata.get("evidence_step_id") or "").strip()
    bound_run_id = str(source.metadata.get("evidence_run_id") or "").strip()
    if bound_step_id:
        environment, run, step, _reference = _evidence_reference_from_step(
            context,
            environment_id=environment_id,
            step_id=bound_step_id,
            require_semantic_source=True,
        )
        if step.after_frame_id != source.id:
            raise click.ClickException(
                "artifact canonical evidence_step_id does not bind this source"
            )
        if bound_run_id and run.id != bound_run_id:
            raise click.ClickException(
                "artifact canonical evidence_run_id does not match its source step"
            )
        return environment, run, step, source

    candidates: list[tuple[str, str, str, str]] = []
    for run in store.list_evidence_runs(limit=1000):
        if (
            run.status != "passed"
            or not run.ended_at
            or run.scope_id != environment_id
            or source.id not in run.artifact_ids
        ):
            continue
        candidates.extend(
            (str(step.ended_at), str(run.ended_at), run.id, step.id)
            for step in store.list_evidence_steps(run.id)
            if step.status == "passed" and step.ended_at and step.after_frame_id == source.id
        )
    if not candidates:
        raise click.ClickException(
            "canonical artifact must resolve to a passed source EvidenceStep"
        )
    resolved_step_id = sorted(set(candidates))[-1][3]
    environment, run, step, _reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=resolved_step_id,
        require_semantic_source=True,
    )
    return environment, run, step, source


def _locator_agent_brief_rank(item: Mapping[str, Any]) -> tuple[bool, bool, bool, str]:
    """Keep detector-backed controls ahead of speculative navigation hints."""

    element_id = str(item.get("element_id") or item.get("id") or "")
    detector_backed = (
        element_id.startswith("omniparser.element.")
        or item.get("interactivity_source") == "omniparser"
    )
    return (
        item.get("interaction_candidate") is not True,
        not detector_backed,
        item.get("interactivity") is not True,
        element_id,
    )


def _filter_locator_elements(
    elements: Iterable[Mapping[str, Any]],
    *,
    region: tuple[int, int, int, int] | None,
    candidate_only: bool,
) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    for item in elements:
        if candidate_only and item.get("interaction_candidate") is not True:
            continue
        if region is not None:
            bounds = item.get("bounds") or item.get("source_bounds") or {}
            x = int(bounds.get("x") or 0)
            y = int(bounds.get("y") or 0)
            width = int(bounds.get("width") or 0)
            height = int(bounds.get("height") or 0)
            region_x, region_y, region_width, region_height = region
            if not (
                x < region_x + region_width
                and x + width > region_x
                and y < region_y + region_height
                and y + height > region_y
            ):
                continue
            if item.get("interaction_candidate") is True and not (
                region_x * 2 <= x * 2 + width < (region_x + region_width) * 2
                and region_y * 2 <= y * 2 + height < (region_y + region_height) * 2
            ):
                # A one-pixel boundary touch must not make an outside control a
                # candidate for a task-scoped semantic anchor. Keep ordinary
                # text/layout intersection behavior for visual understanding.
                continue
        selected.append(item)
    return selected


@player_observe.command("locate")
@click.option("--environment", "environment_id", required=True)
@click.option("--source-step", "source_step_id", default=None)
@click.option("--artifact", "artifact_id", default=None)
@click.option("--box-threshold", type=click.FloatRange(0.001, 1), default=0.05)
@click.option("--timeout", "timeout_seconds", type=click.FloatRange(1, 120), default=90)
@click.option(
    "--region",
    type=int,
    nargs=4,
    default=None,
    metavar="X Y WIDTH HEIGHT",
    help=(
        "In agent-brief output, keep only elements intersecting this source-image "
        "region; interaction candidates must have their center inside it."
    ),
)
@click.option(
    "--candidate-only",
    is_flag=True,
    help="In agent-brief output, keep only interaction candidates.",
)
@click.option(
    "--omniparser-home",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=None,
)
@click.pass_obj
def player_observe_locate(
    context: _PlayerCLIContext,
    environment_id: str,
    source_step_id: str | None,
    artifact_id: str | None,
    box_threshold: float,
    timeout_seconds: float,
    region: tuple[int, int, int, int] | None,
    candidate_only: bool,
    omniparser_home: Path | None,
) -> None:
    """Run cached OmniParser element location on one canonical source image."""

    if region is not None and (
        region[0] < 0 or region[1] < 0 or region[2] <= 0 or region[3] <= 0
    ):
        raise click.ClickException("region must be non-negative X/Y and positive WIDTH/HEIGHT")
    if os.environ.get(EXTERNAL_AGENT_INVOCATION_ID_ENV):
        # A cold local daemon commonly spends tens of seconds loading models.
        # Preserve enough room for both warmup and the first inference even if
        # an Agent copied an obsolete 30-second example.
        timeout_seconds = max(
            timeout_seconds,
            _EXTERNAL_LOCATOR_TIMEOUT_FLOOR_SECONDS,
        )
    _environment, run, step, source = _canonical_locator_source(
        context,
        environment_id=environment_id,
        source_step_id=source_step_id,
        artifact_id=artifact_id,
    )
    service = CanonicalVisualLocatorService(
        context.facility().store,
        omniparser_home=omniparser_home,
    )
    try:
        result = service.locate(
            source=source,
            environment_id=environment_id,
            source_step_id=step.id,
            evidence_run_id=run.id,
            width=run.viewport_width,
            height=run.viewport_height,
            box_threshold=box_threshold,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - normalize isolated locator failures
        raise click.ClickException(str(exc)) from exc
    if context.agent_brief:
        full = {
            "ok": True,
            "schema": "game-observatory.ai-player.visual-location.v1",
            "locator_result": result,
        }
        receipt = _atomic_agent_receipt(
            context,
            category="locate",
            payload=full,
        )
        filtered_elements = _filter_locator_elements(
            result["elements"],
            region=region,
            candidate_only=candidate_only,
        )
        elements = sorted(filtered_elements, key=_locator_agent_brief_rank)[:8]
        top_elements = [
            {
                "element_id": item.get("element_id") or item.get("id"),
                "bounds": item.get("bounds") or item.get("source_bounds"),
                "content": _one_line_agent_summary(item.get("content") or "", limit=64),
                "type": item.get("type"),
                "interaction_candidate": item.get("interaction_candidate") is True,
            }
            for item in elements
        ]
        preview = result.get("agent_preview") or {}
        brief = {
            "schema": "game-observatory.ai-player.locate-brief.v1",
            "ok": True,
            "locator_result_id": result["id"],
            # Keep the full-result access shape understood by an already-running
            # external Agent session.  Both keys share the same bounded list, so
            # compatibility does not reintroduce the unbounded locator payload.
            "locator_result": {
                "id": result["id"],
                "elements": top_elements,
            },
            "cache_hit": result["cache_hit"],
            "source_step_id": step.id,
            "annotated_preview": {
                "path": preview.get("path"),
                "sha256": preview.get("sha256"),
            },
            "elements": top_elements,
            "selection": {
                "region": list(region) if region is not None else None,
                "candidate_only": candidate_only,
                "matched_element_count": len(filtered_elements),
                "returned_element_count": len(top_elements),
                "total_element_count": len(result["elements"]),
            },
            "cold_timeout_seconds": _EXTERNAL_LOCATOR_TIMEOUT_FLOOR_SECONDS,
            "receipt_ref": receipt["path"],
            "receipt_sha256": receipt["sha256"],
        }
        while (
            len(json.dumps(brief, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            > _LOCATOR_AGENT_BRIEF_MAX_BYTES
            and brief["elements"]
        ):
            brief["elements"].pop()
            brief["selection"]["returned_element_count"] = len(brief["elements"])
        _emit_compact_json(context, brief)
        return
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.visual-location.v1",
            "locator_result": result,
        },
        summary=(
            f"located {len(result['elements'])} elements from {step.id}"
            + (" (cache hit)" if result["cache_hit"] else "")
        ),
    )


def _action_agent_support(
    context: _PlayerCLIContext,
    payload: dict[str, Any],
) -> dict[str, Any]:
    after_preview = None
    step_id = str(payload.get("evidence_step_id") or "")
    artifact_id = str(payload.get("after_artifact_id") or "")
    facility = context.facility()
    artifact = facility.store.get_artifact(artifact_id) if artifact_id else None
    step = facility.store.get_evidence_step(step_id) if step_id else None
    if artifact is not None and step is not None:
        try:
            preview, _mapping = _agent_preview(
                facility,
                source_frame=artifact,
                environment_id=step.metadata.get("environment_id")
                or facility.store.get_evidence_run(step.evidence_run_id).scope_id,
                binding_suffix=f"action-{hashlib.sha256(step.id.encode()).hexdigest()[:16]}",
                source_width=step.viewport_width,
                source_height=step.viewport_height,
            )
            after_preview = {"path": preview.path, "sha256": preview.sha256}
        except (OSError, ValueError, KeyError, AttributeError):
            after_preview = None
    target_effect = payload.get("target_effect")
    return {
        "outcome": (
            "expectation_met" if payload.get("expectation_met") is True else "expectation_missed"
        ),
        "after_preview": after_preview,
        "global_effect": payload.get("global_visual_distance", payload.get("visual_distance")),
        "local_effect": (
            target_effect.get("visual_distance") if isinstance(target_effect, dict) else None
        ),
        "evaluation_source": payload.get("evaluation_source") or "unreported",
        "effect_scope": payload.get("effect_scope") or _AGENT_EFFECT_SCOPE,
        "semantic_success": "unverified",
    }


def _emit_guarded_action(
    context: _PlayerCLIContext,
    payload: dict[str, Any],
    *,
    label: str,
) -> None:
    if context.agent_brief:
        support = _action_agent_support(context, payload)
        source_step_usable = bool(payload.get("ok"))
        canonical_session = payload.get("canonical_session")
        recovery_environment_id = (
            canonical_session.get("environment_id")
            if isinstance(canonical_session, dict)
            else getattr(canonical_session, "environment_id", None)
        )
        recovery_command = None
        if (
            not source_step_usable
            and support["outcome"] == "expectation_met"
            and isinstance(recovery_environment_id, str)
        ):
            recovery_command = (
                "omni game player --json --agent-brief observe capture "
                f"--environment {recovery_environment_id} --focus"
            )
        full = {
            "ok": True,
            "schema": "game-observatory.ai-player.guarded-action-result.v1",
            "action": payload,
            "agent_support": support,
        }
        receipt = None
        receipt_status = "available"
        try:
            receipt = _atomic_agent_receipt(
                context,
                category="actions",
                payload=full,
            )
        except PermissionError:
            # The canonical EvidenceStep and transition are already durable before
            # this presentation receipt is written. A transient Windows sharing
            # denial must not turn a completed device action into an unknown action
            # that forces the Agent to re-observe or risks a duplicate click.
            receipt_status = "temporarily_unavailable"
        brief = {
            "schema": "game-observatory.ai-player.action-brief.v1",
            "ok": bool(payload.get("ok")),
            "outcome": support["outcome"],
            "evidence_step_id": payload.get("evidence_step_id"),
            "after_preview": support["after_preview"],
            **(
                {
                    "source_step_usable": False,
                    "recovery_command": recovery_command,
                }
                if recovery_command
                else {
                    "global_effect": support["global_effect"],
                    "local_effect": support["local_effect"],
                    "evaluation_source": support["evaluation_source"],
                    "effect_scope": support["effect_scope"],
                    "semantic_ok": payload.get("memory_status")
                    in {"persisted", "handled_by_skill_runtime"},
                }
            ),
            "remaining_actions": payload.get("session_remaining_actions"),
            "stop": bool(payload.get("stop_recommended")),
            "receipt_ref": receipt["path"] if receipt is not None else None,
            "receipt_sha256": receipt["sha256"] if receipt is not None else None,
            **({"receipt_status": receipt_status} if receipt_status != "available" else {}),
        }
        encoded = json.dumps(brief, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 1000:
            raise click.ClickException(
                f"Agent action brief exceeded 1000 bytes ({len(encoded)} bytes)"
            )
        _emit_compact_json(context, brief)
        if not payload["ok"]:
            raise click.exceptions.Exit(1)
        return
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guarded-action-result.v1",
            "action": payload,
        },
        summary=f"{label}：{'符合预期' if payload['ok'] else '未满足预期，已建议停止'}",
    )
    if not payload["ok"]:
        raise click.exceptions.Exit(1)


def _preview_tap_to_source_pixels(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    source_step_id: str,
    x: int,
    y: int,
    bounds: tuple[int, int, int, int],
) -> tuple[int, int, SourcePixelRect, dict[str, int | float | str]]:
    """Resolve a source-step preview and map one preview-space tap to source pixels."""

    _environment, run, step, _reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=source_step_id,
        require_semantic_source=True,
    )
    store = context.facility().store
    source = store.get_artifact(str(step.after_frame_id))
    if source is None:
        raise click.ClickException("来源 EvidenceStep 缺少原图。")
    source_origin_id = str(source.metadata.get("source_artifact_id") or source.id)
    preview_candidates = [
        artifact
        for artifact_id in run.artifact_ids
        if (artifact := store.get_artifact(artifact_id)) is not None
    ]
    # Guarded actions create their compact after-preview after the EvidenceRun has
    # already been committed, so that derived preview is intentionally absent from
    # run.artifact_ids.  Its content-addressed binding is deterministic and can be
    # safely recovered from the terminal action step.  Accepting it lets an Agent
    # inspect an action result and immediately use that exact image for the next
    # tap, without an otherwise redundant observe capture.
    action_preview_id = (
        f"art.agent-preview.action-{hashlib.sha256(step.id.encode()).hexdigest()[:16]}"
    )
    action_preview = store.get_artifact(action_preview_id)
    if action_preview is not None:
        preview_candidates.append(action_preview)
    previews = list(
        {
            artifact.id: artifact
            for artifact in preview_candidates
            if artifact.metadata.get("role") == "agent_preview"
            and artifact.metadata.get("environment_id") == environment_id
            and str(artifact.metadata.get("source_artifact_id") or "") == source_origin_id
        }.values()
    )
    if len(previews) != 1:
        raise click.ClickException("来源 EvidenceStep 必须关联且只关联一个 agent preview。")
    preview = previews[0]
    preview_path = Path(preview.path)
    if not preview_path.is_file():
        raise click.ClickException("agent preview 文件不存在。")
    preview_raw = preview_path.read_bytes()
    if hashlib.sha256(preview_raw).hexdigest() != preview.sha256:
        raise click.ClickException("agent preview hash 不一致。")
    with Image.open(preview_path) as preview_image:
        actual_preview_width, actual_preview_height = preview_image.size
    metadata = preview.metadata
    source_width = int(metadata.get("source_width") or 0)
    source_height = int(metadata.get("source_height") or 0)
    preview_width = int(metadata.get("preview_width") or 0)
    preview_height = int(metadata.get("preview_height") or 0)
    if (source_width, source_height) != (run.viewport_width, run.viewport_height):
        raise click.ClickException("agent preview 登记的原图尺寸与 EvidenceRun 不一致。")
    if (preview_width, preview_height) != (
        actual_preview_width,
        actual_preview_height,
    ):
        raise click.ClickException("agent preview 登记尺寸与文件不一致。")
    bx, by, bw, bh = bounds
    if bw <= 0 or bh <= 0:
        raise click.ClickException("预览 bounds 的宽高必须大于零。")
    if not (0 <= bx and 0 <= by and bx + bw <= preview_width and by + bh <= preview_height):
        raise click.ClickException("预览 bounds 超出 agent preview。")
    if not (bx <= x < bx + bw and by <= y < by + bh):
        raise click.ClickException("预览点击点不在声明的 bounds 内。")
    scale_x = source_width / preview_width
    scale_y = source_height / preview_height
    source_x = min(source_width - 1, round(x * scale_x))
    source_y = min(source_height - 1, round(y * scale_y))
    source_left = math.floor(bx * scale_x)
    source_top = math.floor(by * scale_y)
    source_right = min(source_width, math.ceil((bx + bw) * scale_x))
    source_bottom = min(source_height, math.ceil((by + bh) * scale_y))
    source_bounds = SourcePixelRect(
        x=source_left,
        y=source_top,
        width=source_right - source_left,
        height=source_bottom - source_top,
    )
    if not source_bounds.contains(SourcePixelPoint(x=source_x, y=source_y)):
        raise click.ClickException("换算后的原图点击点不在原图 bounds 内。")
    mapping: dict[str, int | float | str] = {
        "preview_artifact_id": preview.id,
        "preview_width": preview_width,
        "preview_height": preview_height,
        "source_width": source_width,
        "source_height": source_height,
        "source_pixel_scale_x": scale_x,
        "source_pixel_scale_y": scale_y,
    }
    return source_x, source_y, source_bounds, mapping


@cmd_player.group("act")
def player_act() -> None:
    """执行一项带来源画面、预期、预算、账号策略和完整证据的动作。"""


@player_act.command("tap")
@click.argument("x", type=int)
@click.argument("y", type=int)
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--target", "target_name", required=True, help="人类可读的点击目标。")
@click.option("--bounds", nargs=4, type=int, required=True, metavar="X Y W H")
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-no-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.option(
    "--dynamic-scene",
    is_flag=True,
    help="允许持续界面动画，仍要求目标区域稳定且动态终态通过。",
)
@click.pass_obj
def player_act_tap(
    context: _PlayerCLIContext,
    x: int,
    y: int,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    target_name: str,
    bounds: tuple[int, int, int, int],
    expectation_summary: str,
    expect_no_change: bool,
    min_visual_distance: float,
    dynamic_scene: bool,
) -> None:
    """在原图坐标点击目标框；source mismatch 时拒绝执行。"""

    action = NormalizedAction(type="tap", x=x, y=y)
    target_rect = SourcePixelRect(
        x=bounds[0],
        y=bounds[1],
        width=bounds[2],
        height=bounds[3],
    )
    if (
        not expect_no_change
        and not dynamic_scene
        and _try_replay_known_atomic_action(
            context,
            environment_id=environment_id,
            session_id=session_id,
            source_step_id=source_step_id,
            action=action,
            target_bounds=target_rect,
            target_name=target_name,
        )
    ):
        return
    payload = _execute_guarded_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=action,
        target_name=target_name,
        target_bounds=target_rect,
        expectation_summary=expectation_summary,
        expect_change=not expect_no_change,
        min_visual_distance=min_visual_distance,
        dynamic_scene=dynamic_scene,
    )
    _emit_guarded_action(context, payload, label=f"点击 {target_name}")


@player_act.command("tap-anchor")
@click.argument("anchor_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", default=None)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--target", "target_name", default=None)
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-no-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.option("--dynamic-scene", is_flag=True)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Resolve the reviewed anchor without opening a device session.",
)
@click.pass_obj
def player_act_tap_anchor(
    context: _PlayerCLIContext,
    anchor_id: str,
    environment_id: str,
    session_id: str | None,
    source_step_id: str,
    target_name: str | None,
    expectation_summary: str,
    expect_no_change: bool,
    min_visual_distance: float,
    dynamic_scene: bool,
    dry_run: bool,
) -> None:
    """Resolve one reviewed semantic anchor into the existing guarded tap path."""

    player = context.player()
    state, state_source = resolve_current_semantic_state(
        player,
        environment_id=environment_id,
    )
    if state is None:
        raise click.ClickException("current semantic state is unavailable")
    try:
        plan = resolve_surface_anchor_action_plan(
            player,
            environment_id=environment_id,
            state_id=state.id,
            source_step_id=source_step_id,
            anchor_id=anchor_id,
        )
    except SurfaceAnchorActionError as exc:
        raise click.ClickException(str(exc)) from exc

    resolved_target_name = target_name or " / ".join(plan.target_tokens)
    anchor_selection = {
        **plan.model_dump(mode="json", by_alias=True),
        "current_state_source": state_source,
    }
    if dry_run:
        _emit(
            context,
            {
                "schema": "game-observatory.ai-player.surface-anchor-action-resolution.v1",
                "plan": anchor_selection,
            },
            summary=f"已解析语义锚点：{anchor_id}",
        )
        return
    if plan.requires_visual_relocalization:
        bounds = plan.source_pixel_bounds
        locate_command = (
            "omni game player --json --agent-brief observe locate "
            f"--environment {environment_id} --source-step {source_step_id} "
            f"--region {bounds.x} {bounds.y} {bounds.width} {bounds.height} "
            "--candidate-only"
        )
        raise click.ClickException(
            "dynamic surface anchor requires current-frame visual relocalization; "
            f"run: {locate_command}"
        )
    if session_id is None:
        raise click.ClickException("--session is required when executing a fixed anchor")
    point = plan.source_point
    if point is None:  # pragma: no cover - enforced by SurfaceAnchorActionPlanV1
        raise click.ClickException("fixed surface anchor did not resolve a source point")
    action = NormalizedAction(type="tap", x=point.x, y=point.y)
    if (
        not expect_no_change
        and not dynamic_scene
        and _try_replay_known_atomic_action(
            context,
            environment_id=environment_id,
            session_id=session_id,
            source_step_id=source_step_id,
            action=action,
            target_bounds=plan.source_pixel_bounds,
            target_name=resolved_target_name,
        )
    ):
        return
    payload = _execute_guarded_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=action,
        target_name=resolved_target_name,
        target_bounds=plan.source_pixel_bounds,
        expectation_summary=expectation_summary,
        expect_change=not expect_no_change,
        min_visual_distance=min_visual_distance,
        dynamic_scene=dynamic_scene,
        source_semantic_state_id=plan.state_id,
        source_state_observation_id=plan.source_observation_id,
    )
    _emit_guarded_action(
        context,
        {**payload, "surface_anchor_selection": anchor_selection},
        label=f"点击语义锚点 {resolved_target_name}",
    )


@player_act.command("tap-preview")
@click.argument("x", type=int)
@click.argument("y", type=int)
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--target", "target_name", required=True, help="人类可读的点击目标。")
@click.option("--bounds", nargs=4, type=int, required=True, metavar="X Y W H")
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-no-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.option(
    "--dynamic-scene",
    is_flag=True,
    help="允许持续界面动画，仍要求目标区域稳定且动态终态通过。",
)
@click.pass_obj
def player_act_tap_preview(
    context: _PlayerCLIContext,
    x: int,
    y: int,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    target_name: str,
    bounds: tuple[int, int, int, int],
    expectation_summary: str,
    expect_no_change: bool,
    min_visual_distance: float,
    dynamic_scene: bool,
) -> None:
    """使用 agent preview 坐标点击；CLI 负责换算并复用原图动作守卫。"""

    source_x, source_y, source_bounds, mapping = _preview_tap_to_source_pixels(
        context,
        environment_id=environment_id,
        source_step_id=source_step_id,
        x=x,
        y=y,
        bounds=bounds,
    )
    action = NormalizedAction(type="tap", x=source_x, y=source_y)
    if (
        not expect_no_change
        and not dynamic_scene
        and _try_replay_known_atomic_action(
            context,
            environment_id=environment_id,
            session_id=session_id,
            source_step_id=source_step_id,
            action=action,
            target_bounds=source_bounds,
            target_name=target_name,
        )
    ):
        return
    payload = _execute_guarded_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=action,
        target_name=target_name,
        target_bounds=source_bounds,
        expectation_summary=expectation_summary,
        expect_change=not expect_no_change,
        min_visual_distance=min_visual_distance,
        dynamic_scene=dynamic_scene,
    )
    _emit_guarded_action(
        context,
        {
            **payload,
            "preview_input": {"x": x, "y": y, "bounds": list(bounds)},
            "preview_coordinate_mapping": mapping,
            "source_input": {
                "x": source_x,
                "y": source_y,
                "bounds": source_bounds.model_dump(mode="json"),
            },
        },
        label=f"点击 {target_name}",
    )


@player_act.command("tap-element")
@click.argument("locator_result_id")
@click.argument("element_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--anchor-state", "anchor_state_id", default=None)
@click.option("--anchor", "anchor_id", default=None)
@click.option("--target", "target_name", required=True)
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-no-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.option("--dynamic-scene", is_flag=True)
@click.pass_obj
def player_act_tap_element(
    context: _PlayerCLIContext,
    locator_result_id: str,
    element_id: str,
    environment_id: str,
    session_id: str,
    anchor_state_id: str | None,
    anchor_id: str | None,
    target_name: str,
    expectation_summary: str,
    expect_no_change: bool,
    min_visual_distance: float,
    dynamic_scene: bool,
) -> None:
    """Tap the center of one persisted canonical locator element without pixel input."""

    if bool(anchor_state_id) != bool(anchor_id):
        raise click.ClickException("--anchor-state and --anchor must be provided together")
    service = CanonicalVisualLocatorService(context.facility().store)
    try:
        locator_result = service.load(locator_result_id)
    except Exception as exc:  # noqa: BLE001 - normalize persisted locator failures
        raise click.ClickException(str(exc)) from exc
    source_binding = locator_result["source"]
    if source_binding.get("environment_id") != environment_id:
        raise click.ClickException("locator result belongs to another environment")
    source_step_id = str(source_binding.get("source_step_id") or "")
    try:
        _environment, run, step, source = _canonical_locator_source(
            context,
            environment_id=environment_id,
            source_step_id=source_step_id,
            artifact_id=None,
        )
    except Exception as exc:  # noqa: BLE001 - expose source guard failures as CLI errors
        raise click.ClickException(str(exc)) from exc
    if (
        source_binding.get("evidence_run_id") != run.id
        or source_binding.get("source_artifact_id") != source.id
        or source_binding.get("source_sha256") != source.sha256
        or int(source_binding.get("width") or 0) != run.viewport_width
        or int(source_binding.get("height") or 0) != run.viewport_height
    ):
        raise click.ClickException("locator result no longer matches its canonical source step")
    source_path = Path(source.path)
    if (
        not source_path.is_file()
        or hashlib.sha256(source_path.read_bytes()).hexdigest() != source.sha256
    ):
        raise click.ClickException("canonical locator source file hash changed")
    matching = [item for item in locator_result["elements"] if item.get("element_id") == element_id]
    if len(matching) != 1:
        raise click.ClickException("locator element ID is absent or ambiguous")
    element = matching[0]
    if element.get("interaction_candidate") is not True:
        raise click.ClickException("locator element is not an interaction candidate")
    bounds = SourcePixelRect.model_validate(element["bounds"])
    center = SourcePixelPoint(
        x=bounds.x + bounds.width // 2,
        y=bounds.y + bounds.height // 2,
    )
    if not bounds.contains(center):
        raise click.ClickException("locator element center is outside its canonical bounds")
    anchor_plan = None
    if anchor_state_id is not None and anchor_id is not None:
        try:
            anchor_plan = guard_locator_element_for_surface_anchor(
                context.player(),
                environment_id=environment_id,
                state_id=anchor_state_id,
                source_step_id=step.id,
                anchor_id=anchor_id,
                element_bounds=bounds,
            )
        except SurfaceAnchorActionError as exc:
            raise click.ClickException(str(exc)) from exc
    action = NormalizedAction(type="tap", x=center.x, y=center.y)
    if (
        not expect_no_change
        and not dynamic_scene
        and _try_replay_known_atomic_action(
            context,
            environment_id=environment_id,
            session_id=session_id,
            source_step_id=step.id,
            action=action,
            target_bounds=bounds,
            target_name=target_name,
        )
    ):
        return
    payload = _execute_guarded_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=step.id,
        action=action,
        target_name=target_name,
        target_bounds=bounds,
        expectation_summary=expectation_summary,
        expect_change=not expect_no_change,
        min_visual_distance=min_visual_distance,
        dynamic_scene=dynamic_scene,
    )
    _emit_guarded_action(
        context,
        {
            **payload,
            "locator_selection": {
                "locator_result_id": locator_result_id,
                "element_id": element_id,
                "content": element["content"],
                "type": element["type"],
                "source_center": center.model_dump(mode="json"),
                "source_bounds": bounds.model_dump(mode="json"),
                "surface_anchor_guard": (
                    {
                        "state_id": anchor_plan.state_id,
                        "anchor_id": anchor_plan.anchor_id,
                        "anchor_bounds": anchor_plan.source_pixel_bounds.model_dump(
                            mode="json"
                        ),
                    }
                    if anchor_plan is not None
                    else None
                ),
            },
        },
        label=f"鐐瑰嚮 {target_name}",
    )


@player_act.command("swipe")
@click.argument("x", type=int)
@click.argument("y", type=int)
@click.argument("x2", type=int)
@click.argument("y2", type=int)
@click.option("--duration-ms", type=click.IntRange(50, 5000), default=250)
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--target", "target_name", required=True)
@click.option("--bounds", nargs=4, type=int, required=True, metavar="X Y W H")
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-no-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.pass_obj
def player_act_swipe(
    context: _PlayerCLIContext,
    x: int,
    y: int,
    x2: int,
    y2: int,
    duration_ms: int,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    target_name: str,
    bounds: tuple[int, int, int, int],
    expectation_summary: str,
    expect_no_change: bool,
    min_visual_distance: float,
) -> None:
    """在声明的源图区域内滑动，并核对终态变化。"""

    payload = _execute_guarded_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=NormalizedAction(
            type="swipe",
            x=x,
            y=y,
            x2=x2,
            y2=y2,
            duration_ms=duration_ms,
        ),
        target_name=target_name,
        target_bounds=SourcePixelRect(x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3]),
        expectation_summary=expectation_summary,
        expect_change=not expect_no_change,
        min_visual_distance=min_visual_distance,
    )
    _emit_guarded_action(context, payload, label=f"滑动 {target_name}")


def _execute_keylike_action(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    action: NormalizedAction,
    target_name: str,
    expectation_summary: str,
    expect_change: bool,
    min_visual_distance: float,
) -> None:
    payload = _execute_guarded_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=action,
        target_name=target_name,
        target_bounds=None,
        expectation_summary=expectation_summary,
        expect_change=expect_change,
        min_visual_distance=min_visual_distance,
    )
    _emit_guarded_action(context, payload, label=target_name)


@player_act.command("back")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-no-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.pass_obj
def player_act_back(
    context: _PlayerCLIContext,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    expectation_summary: str,
    expect_no_change: bool,
    min_visual_distance: float,
) -> None:
    """触发系统返回，并保留完整前后证据。"""

    _execute_keylike_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=NormalizedAction(type="back"),
        target_name="系统返回",
        expectation_summary=expectation_summary,
        expect_change=not expect_no_change,
        min_visual_distance=min_visual_distance,
    )


@player_act.command("launch")
@click.argument("package")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--expect", "expectation_summary", required=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.pass_obj
def player_act_launch(
    context: _PlayerCLIContext,
    package: str,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    expectation_summary: str,
    min_visual_distance: float,
) -> None:
    """启动当前环境的游戏包，并核对前台画面和完整动作证据。"""

    _execute_keylike_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=NormalizedAction(type="launch", package=package),
        target_name=f"启动游戏包 {package}",
        expectation_summary=expectation_summary,
        expect_change=True,
        min_visual_distance=min_visual_distance,
    )


@player_act.command("text")
@click.argument("value")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-no-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.01)
@click.pass_obj
def player_act_text(
    context: _PlayerCLIContext,
    value: str,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    expectation_summary: str,
    expect_no_change: bool,
    min_visual_distance: float,
) -> None:
    """向当前游戏输入框输入文本；账号发言仍受账号政策约束。"""

    _execute_keylike_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=NormalizedAction(type="text", text=value),
        target_name="输入文本",
        expectation_summary=expectation_summary,
        expect_change=not expect_no_change,
        min_visual_distance=min_visual_distance,
    )


@player_act.command("wait")
@click.argument("seconds", type=click.FloatRange(0.1, 30))
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--expect", "expectation_summary", required=True)
@click.option("--expect-change", is_flag=True)
@click.option("--min-visual-distance", type=click.FloatRange(0, 1), default=0.03)
@click.pass_obj
def player_act_wait(
    context: _PlayerCLIContext,
    seconds: float,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    expectation_summary: str,
    expect_change: bool,
    min_visual_distance: float,
) -> None:
    """等待短暂动画或加载；默认预期稳定不变。"""

    _execute_keylike_action(
        context,
        environment_id=environment_id,
        session_id=session_id,
        source_step_id=source_step_id,
        action=NormalizedAction(type="wait", seconds=seconds),
        target_name=f"等待 {seconds:g} 秒",
        expectation_summary=expectation_summary,
        expect_change=expect_change,
        min_visual_distance=min_visual_distance,
    )


@cmd_player.group("task")
def player_task() -> None:
    """持续任务和探索前沿。"""


@player_task.command("list")
@click.option("--environment", "environment_id", required=True)
@click.option("--status", default=None, help="逗号分隔的状态筛选。")
@click.pass_obj
def player_task_list(
    context: _PlayerCLIContext,
    environment_id: str,
    status: str | None,
) -> None:
    """列出任务目标、退出条件、预算、阻断和证据。"""

    statuses = tuple(item.strip() for item in status.split(",") if item.strip()) if status else None
    tasks = context.player().list_tasks(environment_id, statuses=statuses)
    _emit(
        context,
        {"schema": "game-observatory.ai-player.task-list.v1", "tasks": tasks},
        summary=f"任务：{len(tasks)} 个",
    )


@player_task.command("add")
@click.option("--environment", "environment_id", required=True)
@click.option("--title", required=True)
@click.option(
    "--source",
    type=click.Choice(
        [
            "user_goal",
            "unknown_interaction",
            "missing_transition",
            "stale_memory",
            "interface_family_gap",
            "new_unlock",
            "guide_update",
            "failed_skill",
            "gameplay_candidate",
            "coverage_gap",
        ]
    ),
    default="coverage_gap",
    show_default=True,
)
@click.option("--reason", required=True, help="要补齐的内容和可观察退出条件。")
@click.option("--source-step", "source_step_id", required=True)
@click.option("--id", "task_id", default=None)
@click.option("--dependency", "dependency_task_ids", multiple=True)
@click.option("--action-budget", type=click.IntRange(1), default=10, show_default=True)
@click.option("--token-budget", type=click.IntRange(1), default=None)
@click.option("--time-budget", type=click.FloatRange(min=1), default=600, show_default=True)
@click.option("--value", "value_score", type=float, default=0.0)
@click.option("--novelty", "novelty_score", type=float, default=0.0)
@click.option("--coverage", "expected_coverage_gain", type=float, default=0.0)
@click.option("--risk", "risk_score", type=click.FloatRange(min=0), default=0.0)
@click.option("--max-attempts", type=click.IntRange(1), default=1)
@click.pass_obj
def player_task_add(
    context: _PlayerCLIContext,
    environment_id: str,
    title: str,
    source: str,
    reason: str,
    source_step_id: str,
    task_id: str | None,
    dependency_task_ids: tuple[str, ...],
    action_budget: int,
    token_budget: int | None,
    time_budget: float,
    value_score: float,
    novelty_score: float,
    expected_coverage_gain: float,
    risk_score: float,
    max_attempts: int,
) -> None:
    """从已留证界面新增一项有预算、有退出条件的探索任务。"""

    _environment, _run, _step, reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=source_step_id,
    )
    task = FrontierTaskV1(
        id=task_id or f"task.cli.{uuid.uuid4().hex}",
        environment_id=environment_id,
        evidence_refs=[reference],
        title=title,
        source=source,
        reason=reason,
        dependency_task_ids=list(dependency_task_ids),
        value_score=value_score,
        novelty_score=novelty_score,
        expected_coverage_gain=expected_coverage_gain,
        risk_score=risk_score,
        action_budget=action_budget,
        token_budget=token_budget,
        time_budget_seconds=time_budget,
        max_attempts=max_attempts,
    )
    try:
        stored = context.player().enqueue_task(task)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {"schema": "game-observatory.ai-player.task-add-result.v1", "task": stored},
        summary=f"已新增任务：{stored.id}",
    )


def _change_task_status(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    task_id: str,
    allowed_statuses: tuple[str, ...],
    new_status: str,
    updates: dict[str, Any] | None = None,
) -> FrontierTaskV1:
    player = context.player()
    current = player.get_task(environment_id, task_id)
    if current is None:
        raise click.ClickException(f"没有这个任务: {task_id}")
    if current.status not in allowed_statuses:
        raise click.ClickException(
            f"任务 {task_id} 当前为 {current.status}，不能改为 {new_status}。"
        )
    changed = player.compare_and_swap_task_status(
        environment_id,
        task_id,
        current.status,
        new_status,
        expected_version=current.version,
        updates=updates,
    )
    if changed is None:
        raise click.ClickException("任务在更新时发生并发变化，请重新读取后再试。")
    return changed


@player_task.command("claim")
@click.argument("task_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", default=None, help="可选运行中 Session ID。")
@click.pass_obj
def player_task_claim(
    context: _PlayerCLIContext,
    task_id: str,
    environment_id: str,
    session_id: str | None,
) -> None:
    """认领一个依赖已闭合的 queued 任务，并可绑定运行中 Session。"""

    player = context.player()
    current_task = player.get_task(environment_id, task_id)
    if current_task is None:
        raise click.ClickException(f"没有这个任务: {task_id}")
    unresolved = [
        dependency_id
        for dependency_id in current_task.dependency_task_ids
        if (dependency := player.get_task(environment_id, dependency_id)) is None
        or dependency.status != "completed"
    ]
    if unresolved:
        raise click.ClickException("任务依赖尚未闭合: " + ", ".join(unresolved))
    session = None
    if session_id is not None:
        control = AIPlayerSessionControl(player)
        try:
            session = control.assert_session_can_act(environment_id, session_id)
        except AIPlayerSessionError as exc:
            raise click.ClickException(str(exc)) from exc
    task = _change_task_status(
        context,
        environment_id=environment_id,
        task_id=task_id,
        allowed_statuses=("queued",),
        new_status="active",
        updates={"attempt_count": current_task.attempt_count + 1},
    )
    if session is not None:
        session = control.checkpoint(
            session.id,
            AIPlayerSessionCheckpointCommand(
                command_id=f"{session.id}.claim-task.{session.version}.{uuid.uuid4().hex[:12]}",
                environment_id=environment_id,
                expected_version=session.version,
                actor="ai-player-cli",
                reason=f"认领并绑定任务 {task.id}。",
                remaining_action_budget=session.remaining_action_budget,
                remaining_token_budget=session.remaining_token_budget,
                remaining_time_seconds=session.remaining_time_seconds,
                active_task_ids=list(dict.fromkeys([*session.active_task_ids, task.id])),
                last_capsule_id=session.last_capsule_id,
                last_evidence_refs=session.last_evidence_refs,
            ),
        )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.task-status-result.v1",
            "task": task,
            "session": session,
        },
        summary=f"已认领任务：{task.id}",
    )


@player_task.command("complete")
@click.argument("task_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.pass_obj
def player_task_complete(
    context: _PlayerCLIContext,
    task_id: str,
    environment_id: str,
    source_step_id: str,
) -> None:
    """将已经由终态证据闭合的 active 任务标记完成。"""

    current = context.player().get_task(environment_id, task_id)
    if current is None:
        raise click.ClickException(f"没有这个任务: {task_id}")
    _environment, _run, _step, reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=source_step_id,
    )
    references = {
        item.model_dump_json(by_alias=True): item for item in [*current.evidence_refs, reference]
    }
    task = _change_task_status(
        context,
        environment_id=environment_id,
        task_id=task_id,
        allowed_statuses=("active",),
        new_status="completed",
        updates={"evidence_refs": list(references.values())},
    )
    _emit(
        context,
        {"schema": "game-observatory.ai-player.task-status-result.v1", "task": task},
        summary=f"任务已完成：{task.id}",
    )


@player_task.command("block")
@click.argument("task_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--reason", required=True)
@click.option("--reactivate-when", "reactivation_condition", required=True)
@click.pass_obj
def player_task_block(
    context: _PlayerCLIContext,
    task_id: str,
    environment_id: str,
    reason: str,
    reactivation_condition: str,
) -> None:
    """记录明确阻断与恢复条件，避免在同一失败路径上空转。"""

    task = _change_task_status(
        context,
        environment_id=environment_id,
        task_id=task_id,
        allowed_statuses=("queued", "active", "cooldown"),
        new_status="blocked",
        updates={
            "blocked_reason": reason,
            "reactivation_condition": reactivation_condition,
        },
    )
    _emit(
        context,
        {"schema": "game-observatory.ai-player.task-status-result.v1", "task": task},
        summary=f"任务已阻断：{task.id}",
    )


@cmd_player.group("gameplay")
def player_gameplay() -> None:
    """从实机状态、转移与证据归纳待审玩法边界。"""


@player_gameplay.command("list")
@click.option("--environment", "environment_id", required=True)
@click.option(
    "--status",
    type=click.Choice(["candidate", "scope_review", "closed", "invalidated"]),
    default=None,
)
@click.option("--all-versions", is_flag=True, help="包含不可变历史版本。")
@click.pass_obj
def player_gameplay_list(
    context: _PlayerCLIContext,
    environment_id: str,
    status: str | None,
    all_versions: bool,
) -> None:
    """列出玩法候选、边界、规则线索、相邻关系和来源证据。"""

    candidates = context.player().list_gameplay_candidates(
        environment_id,
        latest_only=not all_versions,
    )
    if status is not None:
        candidates = [candidate for candidate in candidates if candidate.status == status]
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.gameplay-candidate-list.v1",
            "candidates": candidates,
        },
        summary=f"玩法候选：{len(candidates)} 个",
    )


@player_gameplay.command("show")
@click.argument("candidate_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--version", type=click.IntRange(1), default=None)
@click.pass_obj
def player_gameplay_show(
    context: _PlayerCLIContext,
    candidate_id: str,
    environment_id: str,
    version: int | None,
) -> None:
    """查看一个玩法候选的完整边界与证据。"""

    candidate = context.player().get_gameplay_candidate(
        environment_id,
        candidate_id,
        version=version,
    )
    if candidate is None:
        raise click.ClickException(f"没有这个玩法候选: {candidate_id}")
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.gameplay-candidate-detail.v1",
            "candidate": candidate,
        },
        summary=f"玩法候选：{candidate.title}",
    )


@player_gameplay.command("invalidate")
@click.argument("candidate_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--reason", required=True, help="边界错误或需要归并的可审计原因。")
@click.pass_obj
def player_gameplay_invalidate(
    context: _PlayerCLIContext,
    candidate_id: str,
    environment_id: str,
    reason: str,
) -> None:
    """保留历史与证据，追加一个自动发现不会覆盖的失效版本。"""

    player = context.player()
    current = player.get_gameplay_candidate(environment_id, candidate_id)
    if current is None:
        raise click.ClickException(f"没有这个玩法候选: {candidate_id}")
    if current.status == "invalidated":
        _emit(
            context,
            {
                "schema": "game-observatory.ai-player.gameplay-candidate-invalidate.v1",
                "candidate": current,
                "unchanged": True,
            },
            summary=f"玩法候选已经失效：{current.title}",
        )
        return
    if current.status == "closed":
        raise click.ClickException("已闭合玩法不能由候选失效命令覆盖。")
    payload = current.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "version": current.version + 1,
            "status": "invalidated",
            "boundary_summary": f"失效原因：{reason} 原候选记录：{current.boundary_summary}",
            "created_at": utc_now(),
        }
    )
    updated = type(current).model_validate(payload)
    try:
        player.append_gameplay_candidate(updated)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.gameplay-candidate-invalidate.v1",
            "candidate": updated,
            "unchanged": False,
        },
        summary=f"玩法候选已失效：{updated.title}",
    )


@player_gameplay.command("discover")
@click.option("--environment", "environment_id", required=True)
@click.option("--recent-edge-limit", type=click.IntRange(2, 5000), default=512)
@click.option("--write-limit", type=click.IntRange(1, 100), default=8)
@click.pass_obj
def player_gameplay_discover(
    context: _PlayerCLIContext,
    environment_id: str,
    recent_edge_limit: int,
    write_limit: int,
) -> None:
    """有界扫描实机轨迹并幂等写入待审玩法候选；不调用模型或设备。"""

    try:
        report = discover_gameplay_candidates(
            context.player(),
            environment_id,
            recent_edge_limit=recent_edge_limit,
            write_limit=write_limit,
        )
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.gameplay-candidate-discovery.v1",
            "environment_id": environment_id,
            "scanned_edge_count": report.scanned_edge_count,
            "eligible_anchor_count": report.eligible_anchor_count,
            "candidate_version_ids": list(report.candidate_version_ids),
            "unchanged_candidate_ids": list(report.unchanged_candidate_ids),
            "review_locked_candidate_ids": list(report.review_locked_candidate_ids),
            "rejected_navigation_edge_count": report.rejected_navigation_edge_count,
            "rejected_incomplete_anchor_count": report.rejected_incomplete_anchor_count,
            "invalid_evidence_edge_count": report.invalid_evidence_edge_count,
        },
        summary=(
            f"扫描 {report.scanned_edge_count} 条边，写入 "
            f"{len(report.candidate_version_ids)} 个玩法候选版本"
        ),
    )


@cmd_player.group("explore")
def player_explore() -> None:
    """在同一个原生外部 Session 内规划、运行、中断和恢复持续探索。"""


@player_explore.command("plan")
@click.option("--environment", "environment_id", required=True)
@click.option("--title", required=True)
@click.option("--reason", required=True, help="目标、可观察退出条件和停止边界。")
@click.option("--source-step", "source_step_id", required=True)
@click.option("--action-budget", type=click.IntRange(1), default=10)
@click.option("--token-budget", type=click.IntRange(1), default=None)
@click.option("--time-budget", type=click.FloatRange(min=1), default=600)
@click.pass_context
def player_explore_plan(
    click_context: click.Context,
    environment_id: str,
    title: str,
    reason: str,
    source_step_id: str,
    action_budget: int,
    token_budget: int | None,
    time_budget: float,
) -> None:
    """把运行前预期、预算和退出条件登记成 canonical 探索任务。"""

    click_context.invoke(
        player_task_add,
        environment_id=environment_id,
        title=title,
        source="coverage_gap",
        reason=reason,
        source_step_id=source_step_id,
        task_id=None,
        dependency_task_ids=(),
        action_budget=action_budget,
        token_budget=token_budget,
        time_budget=time_budget,
        value_score=0.0,
        novelty_score=1.0,
        expected_coverage_gain=1.0,
        risk_score=0.0,
        max_attempts=2,
    )


def _select_explore_focus_general_memories(memories: list[Any]) -> list[Any]:
    """Keep every current prohibition while bounding ordinary focus history."""

    def order(item: Any) -> tuple[int, str, str]:
        return (
            int(getattr(item, "version", 0)),
            str(getattr(item, "created_at", "")),
            str(getattr(item, "id", "")),
        )

    latest_forbidden_by_subject: dict[str, Any] = {}
    ordinary: list[Any] = []
    for item in memories:
        if getattr(item, "status", None) != "active":
            continue
        kind = getattr(item, "kind", None)
        if kind == "failure_forbidden":
            subject_id = str(getattr(item, "subject_id", ""))
            previous = latest_forbidden_by_subject.get(subject_id)
            if previous is None or order(item) > order(previous):
                latest_forbidden_by_subject[subject_id] = item
        elif kind in {"working", "procedural", "semantic"}:
            ordinary.append(item)
    forbidden = sorted(latest_forbidden_by_subject.values(), key=order)
    return [*forbidden, *sorted(ordinary, key=order)[-2:]]


@player_explore.command("run")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--task", "task_id", required=True)
@click.option("--instruction", default=None, help="本轮增量说明；任务正文始终会附带。")
@click.option(
    "--image",
    "image_paths",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    multiple=True,
    help="把最新证据图直接绑定到本轮模型输入，可重复。",
)
@click.option("--timeout", "timeout_seconds", type=click.FloatRange(min=1), default=900)
@click.option(
    "--cwd",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=Path("."),
)
@click.pass_context
def player_explore_run(
    click_context: click.Context,
    session_id: str,
    environment_id: str,
    task_id: str,
    instruction: str | None,
    image_paths: tuple[Path, ...],
    timeout_seconds: float,
    cwd: Path,
) -> None:
    """在既有 provider Session 中执行一段有意义的连续探索。"""

    focus_build_started = time.monotonic()
    focus_build_last = focus_build_started
    focus_build_timings_ms: dict[str, float] = {}

    def mark_focus_build_stage(name: str) -> None:
        nonlocal focus_build_last
        current = time.monotonic()
        focus_build_timings_ms[name] = round((current - focus_build_last) * 1000, 3)
        focus_build_last = current

    context = click_context.find_object(_PlayerCLIContext)
    if context is None:
        raise click.ClickException("缺少 AI 玩家 CLI 上下文。")
    player = context.player()
    environment = player.get_environment(environment_id)
    if environment is None:
        raise click.ClickException(f"没有这个环境: {environment_id}")
    task = player.get_task(environment_id, task_id)
    if task is None:
        raise click.ClickException(f"没有这个任务: {task_id}")
    if task.status == "queued":
        unresolved = [
            dependency_id
            for dependency_id in task.dependency_task_ids
            if (dependency := player.get_task(environment_id, dependency_id)) is None
            or dependency.status != "completed"
        ]
        if unresolved:
            raise click.ClickException("任务依赖尚未闭合: " + ", ".join(unresolved))
        task = _change_task_status(
            context,
            environment_id=environment_id,
            task_id=task_id,
            allowed_statuses=("queued",),
            new_status="active",
            updates={"attempt_count": task.attempt_count + 1},
        )
    if task.status != "active":
        raise click.ClickException(f"探索任务必须处于 active，当前为 {task.status}")
    ledger = _external_ledger(context)
    external = ledger.get_session(session_id)
    if external is None:
        raise click.ClickException(f"外部 Agent Session 不存在: {session_id}")
    if external.environment_id != environment_id:
        raise click.ClickException("外部 Agent Session 属于另一个游戏环境。")
    external = _require_external_contract_current(context, external)
    control = AIPlayerSessionControl(player)
    canonical = _ensure_canonical_session_running(
        control,
        session_id=session_id,
        environment_id=environment_id,
        holder=f"external-agent:{external.provider}:{external.id}",
        lease_ttl_seconds=max(120, int(timeout_seconds) + 60),
    )
    if task.id not in canonical.active_task_ids:
        canonical = control.checkpoint(
            canonical.id,
            AIPlayerSessionCheckpointCommand(
                command_id=(
                    f"{canonical.id}.explore-bind.{canonical.version}.{uuid.uuid4().hex[:12]}"
                ),
                environment_id=environment_id,
                expected_version=canonical.version,
                actor="ai-player-cli",
                reason=f"持续探索绑定任务 {task.id}。",
                remaining_action_budget=canonical.remaining_action_budget,
                remaining_token_budget=canonical.remaining_token_budget,
                remaining_time_seconds=canonical.remaining_time_seconds,
                active_task_ids=list(dict.fromkeys([*canonical.active_task_ids, task.id])),
                last_capsule_id=canonical.last_capsule_id,
                last_evidence_refs=canonical.last_evidence_refs,
            ),
        )
    mark_focus_build_stage("session_preflight")
    general_memories = _select_explore_focus_general_memories(player.list_memories(environment_id))
    task_episodes = [
        item
        for item in player.list_memories(environment_id, subject_id=f"explore-task:{task_id}")
        if item.status == "active" and item.kind == "episodic"
    ][-3:]
    active_memories = [*general_memories, *task_episodes]
    mark_focus_build_stage("memory_recall")
    resolved_serial = _resolve_environment_serial(
        context,
        environment_id=environment_id,
        serial=None,
    )
    expected_package = _expected_environment_package(player, environment_id)
    inspection_before = context.facility().inspect_device(
        resolved_serial,
        package=expected_package,
    )
    current_capture = _capture_environment_observation(
        context,
        environment_id,
        resolved_serial,
    )
    foreground_recovery: dict[str, Any] | None = None
    foreground_before = str(inspection_before.get("foreground_activity") or "unknown")
    package_installed = bool((inspection_before.get("package") or {}).get("installed", True))
    if expected_package is not None and not package_installed:
        raise click.ClickException(f"目标游戏包尚未安装：{expected_package}")
    if expected_package is not None and not foreground_before.startswith(f"{expected_package}/"):
        launch_result = _execute_guarded_action(
            context,
            environment_id=environment_id,
            session_id=session_id,
            source_step_id=current_capture["evidence_step"].id,
            action=NormalizedAction(type="launch", package=expected_package),
            target_name=f"A0 前台恢复：{expected_package}",
            target_bounds=None,
            expectation_summary=f"目标游戏包 {expected_package} 恢复到前台。",
            expect_change=True,
            min_visual_distance=0.03,
        )
        launch_session = launch_result["canonical_session"]
        launch_reference = launch_session.last_evidence_refs[-1]
        corrected_budget = min(
            launch_session.action_budget,
            launch_session.remaining_action_budget + 1,
        )
        if corrected_budget > launch_session.remaining_action_budget:
            canonical = control.correct_action_budget(
                session_id,
                AIPlayerSessionBudgetCorrectionCommand(
                    command_id=(
                        f"{session_id}.lifecycle-budget-correction."
                        f"{launch_session.version}.{uuid.uuid4().hex[:12]}"
                    ),
                    environment_id=environment_id,
                    expected_version=launch_session.version,
                    actor="ai-player-cli",
                    reason="A0 前台恢复是 lifecycle 动作，不占语义探索动作预算。",
                    expected_remaining_action_budget=launch_session.remaining_action_budget,
                    corrected_remaining_action_budget=corrected_budget,
                    evidence_refs=[launch_reference],
                ),
            )
        else:
            canonical = launch_session
        inspection_after = context.facility().inspect_device(
            resolved_serial,
            package=expected_package,
        )
        foreground_after = str(inspection_after.get("foreground_activity") or "unknown")
        if not foreground_after.startswith(f"{expected_package}/"):
            raise click.ClickException(
                f"A0 前台恢复后目标包仍未成为前台：{foreground_before} -> {foreground_after}"
            )
        foreground_recovery = {
            "expected_package": expected_package,
            "foreground_before": foreground_before,
            "foreground_after": foreground_after,
            "evidence_run_id": launch_result["evidence_run_id"],
            "evidence_step_id": launch_result["evidence_step_id"],
            "semantic_budget_restored": True,
        }
        current_capture = _capture_environment_observation(
            context,
            environment_id,
            resolved_serial,
        )
    current_preview = ArtifactRef.model_validate(current_capture["agent_preview"]["artifact"])
    current_mapping = dict(current_capture["agent_preview"]["coordinate_mapping"])
    current_source_step_id = current_capture["evidence_step"].id
    current_capture_step = EvidenceStep.model_validate(current_capture["evidence_step"])
    current_reference = EvidenceReferenceV1(
        environment_id=environment_id,
        artifact_ids=current_capture_step.artifact_ids,
        evidence_run_ids=[current_capture["evidence_run"].id],
        evidence_step_ids=[current_capture_step.id],
        trace_run_ids=current_capture_step.observation_run_ids,
        note="A2 当前权威观察及其语义状态绑定。",
    )
    context.runtime["explore_current_step_id"] = current_source_step_id
    mark_focus_build_stage("device_preflight_and_capture")
    current_state_assignment = None
    current_state_error = None
    try:
        current_state_assignment = _ingest_and_resolve_evidence_state(
            context,
            environment_id=environment_id,
            evidence_refs=[current_reference],
        )
    except Exception as exc:  # noqa: BLE001 - primitive exploration remains available
        current_state_error = str(exc)
    mark_focus_build_stage("state_ingest")
    surface_anchor_actions: tuple[dict[str, object], ...] = ()
    surface_anchor_action_error = None
    surface_locator_hints: tuple[dict[str, object], ...] = ()
    surface_locator_hint_error = None
    reuse_skill_versions: tuple[Any, ...] = ()
    reuse_skill_runs: tuple[Any, ...] = ()
    if current_state_assignment is not None:
        with player.read_session():
            reuse_skill_versions = tuple(
                player.list_path_reuse_skill_versions(environment_id)
            )
            reuse_skill_runs = tuple(player.list_skill_runs(environment_id))
            try:
                surface_anchor_actions = build_task_relevant_surface_anchor_actions(
                    player,
                    environment_id=environment_id,
                    state_id=current_state_assignment.state_id,
                    source_step_id=current_source_step_id,
                    session_id=session_id,
                    task_text=f"{task.title}\n{task.reason}",
                    limit=3,
                    skill_versions=reuse_skill_versions,
                    skill_runs=reuse_skill_runs,
                )
            except SurfaceAnchorActionError as exc:
                surface_anchor_action_error = str(exc)
            try:
                surface_locator_hints = build_task_relevant_consensus_locator_hints(
                    player,
                    environment_id=environment_id,
                    state_id=current_state_assignment.state_id,
                    source_step_id=current_source_step_id,
                    session_id=session_id,
                    task_text=f"{task.title}\n{task.reason}",
                    skill_versions=reuse_skill_versions,
                    limit=2,
                )
            except SurfaceAnchorActionError as exc:
                surface_locator_hint_error = str(exc)
    mark_focus_build_stage("surface_anchor_focus")
    candidate_discovery = None
    candidate_discovery_error = None
    try:
        candidate_discovery = crystallize_repeated_atomic_skill_candidates(
            context.player(),
            environment_id,
            current_state_id=(
                current_state_assignment.state_id if current_state_assignment is not None else None
            ),
        )
    except Exception as exc:  # noqa: BLE001 - semantic play remains available
        candidate_discovery_error = f"{type(exc).__name__}: {exc}"
    mark_focus_build_stage("candidate_discovery")
    with player.read_session():
        applicable_skills, applicable_skill_briefs = _applicable_preferred_skill_briefs(
            context,
            environment_id=environment_id,
            current_state_id=(
                current_state_assignment.state_id
                if current_state_assignment is not None
                else None
            ),
            visual_variant_id=None,
            limit=3,
        )
        mark_focus_build_stage("preferred_skill_match")
        candidate_baseline_tokens, candidate_baseline_latency = _candidate_trial_baseline(
            external
        )
        (
            candidate_skills,
            candidate_skill_briefs,
            learned_route_briefs,
        ) = _applicable_candidate_skill_briefs(
            context,
            environment_id=environment_id,
            session_id=session_id,
            current_state_id=(
                current_state_assignment.state_id
                if current_state_assignment is not None
                else None
            ),
            source_step_id=current_source_step_id,
            baseline_model_input_tokens=candidate_baseline_tokens,
            baseline_decision_latency_ms=candidate_baseline_latency,
            limit=2,
            skill_versions=reuse_skill_versions,
            skill_runs=reuse_skill_runs,
        )
        mark_focus_build_stage("candidate_skill_match")
        recent_actions = _recent_session_action_summaries(
            context,
            environment_id=environment_id,
            session_id=session_id,
            limit=4,
        )
        requested_guide_task_ids = {
            item.task_id
            for item in player.list_guide_refresh_requests(
                environment_id,
                pending_only=False,
            )
        }
        triggered_guide_refresh = triggered_refresh_focus_hint(
            task=task,
            source_step_id=current_source_step_id,
            current_state_id=(
                current_state_assignment.state_id
                if current_state_assignment is not None
                else None
            ),
            already_requested_task_ids=requested_guide_task_ids,
        )
    mark_focus_build_stage("recent_context")
    command_base = f"--environment {environment_id} --session {session_id}"
    source_step_argument = "--source-step <source_step_id>"
    candidate_image_paths = [Path(current_preview.path), *image_paths]
    model_image_paths = tuple(dict.fromkeys(path.resolve() for path in candidate_image_paths))
    focus_packet = {
        "schema": "game-observatory.ai-player.a2-focus-packet.v1",
        "phase_objective": external.phase_id,
        "task": {
            "id": task.id,
            "title": task.title,
            "reason_and_exit": task.reason,
            "attempt_count": task.attempt_count,
            "max_attempts": task.max_attempts,
        },
        "canonical_budget": {
            "remaining_semantic_actions": canonical.remaining_action_budget,
            "remaining_seconds": canonical.remaining_time_seconds,
            "remaining_tokens": canonical.remaining_token_budget,
        },
        "latest_side_effect_evidence": [
            item.model_dump(mode="json", by_alias=True)
            for item in canonical.last_evidence_refs[-1:]
        ],
        "recent_actions": recent_actions,
        "authoritative_current_observation": {
            "image_index": 1,
            "evidence_run_id": current_capture["evidence_run"].id,
            "evidence_step_id": current_capture["evidence_step"].id,
            "source_artifact_id": current_capture["observation"]["frame"].id,
            "preview_artifact_id": current_preview.id,
            "captured_at": current_capture["observation"]["captured_at"],
            "coordinate_mapping": current_mapping,
            "semantic_state_id": (
                current_state_assignment.state_id if current_state_assignment is not None else None
            ),
            "state_assignment_id": (
                current_state_assignment.id if current_state_assignment is not None else None
            ),
            "state_resolution_error": current_state_error,
        },
        "applicable_preferred_skills": applicable_skill_briefs,
        "applicable_learned_routes": learned_route_briefs,
        "applicable_candidate_skills": candidate_skill_briefs,
        "task_relevant_surface_anchor_actions": list(surface_anchor_actions),
        "surface_anchor_action_error": surface_anchor_action_error,
        "task_relevant_locator_hints": list(surface_locator_hints),
        "surface_locator_hint_error": surface_locator_hint_error,
        "candidate_discovery": {
            "eligible_signature_count": (
                candidate_discovery.eligible_signature_count
                if candidate_discovery is not None
                else 0
            ),
            "candidate_version_ids": (
                list(candidate_discovery.candidate_version_ids)
                if candidate_discovery is not None
                else []
            ),
            "atomic_candidate_count": (
                candidate_discovery.atomic_candidate_count if candidate_discovery is not None else 0
            ),
            "flow_candidate_count": (
                candidate_discovery.flow_candidate_count if candidate_discovery is not None else 0
            ),
            "path_shape_count": (
                candidate_discovery.path_shape_count if candidate_discovery is not None else 0
            ),
            "merged_supporting_transition_count": (
                candidate_discovery.merged_supporting_transition_count
                if candidate_discovery is not None
                else 0
            ),
            "ambiguous_terminal_shape_count": (
                candidate_discovery.ambiguous_terminal_shape_count
                if candidate_discovery is not None
                else 0
            ),
            "unresolved_terminal_variant_count": (
                candidate_discovery.unresolved_terminal_variant_count
                if candidate_discovery is not None
                else 0
            ),
            "error": candidate_discovery_error,
        },
        "triggered_guide_refresh": (
            triggered_guide_refresh.model_dump(mode="json")
            if triggered_guide_refresh is not None
            else None
        ),
        "a0_foreground_recovery": foreground_recovery,
        "exact_action_command_templates": {
            "navigate": (
                'omni game player --json --agent-brief navigate "<目标界面或操作>" '
                f"{command_base} {source_step_argument}"
            ),
            "observe_capture": (
                "omni game player --json --agent-brief observe capture --focus "
                f"--environment {environment_id}"
            ),
            "locate_elements": (
                "omni game player --json --agent-brief observe locate "
                f"--environment {environment_id} {source_step_argument} --timeout 90"
            ),
            "launch": (
                "omni game player --json --agent-brief act launch "
                f"{expected_package or '<canonical_package>'} {command_base} "
                f'{source_step_argument} --expect "目标游戏恢复到前台" '
                "--min-visual-distance 0.03"
            ),
            "tap_element": (
                "omni game player --json --agent-brief act tap-element "
                "<locator_result_id> <element_id> "
                f'{command_base} --target "<目标名>" '
                '--expect "<预期变化>" --min-visual-distance <0.001/0.01/0.03>'
            ),
            "tap": (
                "omni game player --json --agent-brief act tap-preview "
                "<preview_x> <preview_y> "
                f'{command_base} {source_step_argument} --target "<目标名>" '
                "--bounds <preview_left> <preview_top> <preview_width> <preview_height> "
                '--expect "<预期变化>" '
                "--min-visual-distance <0.001/0.01/0.03>"
            ),
            "tap_dynamic_scene": (
                "omni game player --json --agent-brief act tap-preview "
                "<preview_x> <preview_y> "
                f'{command_base} {source_step_argument} --target "<目标名>" '
                "--bounds <preview_left> <preview_top> <preview_width> <preview_height> "
                '--expect "<预期变化>" '
                "--min-visual-distance <0.01或0.03> --dynamic-scene"
            ),
            "swipe": (
                "omni game player --json --agent-brief act swipe <x> <y> <x2> <y2> "
                f'{command_base} {source_step_argument} --target "<目标名>" '
                '--bounds <x> <y> <width> <height> --expect "<预期变化>" '
                "--min-visual-distance 0.03"
            ),
            "back": (
                "omni game player --json --agent-brief act back "
                f"{command_base} {source_step_argument} "
                '--expect "<预期变化>" --min-visual-distance 0.03'
            ),
            "wait": (
                "omni game player --json --agent-brief act wait <seconds> "
                f"{command_base} {source_step_argument} "
                '--expect "<等待后应保持的稳定状态>"'
            ),
            "text": (
                'omni game player --json --agent-brief act text "<输入文本>" '
                f'{command_base} {source_step_argument} --expect "<预期变化>" '
                "--min-visual-distance 0.01"
            ),
            **(
                {
                    "skill_replay": (
                        "omni game player --json --agent-brief skill replay "
                        "<preferred_version_id> "
                        f"{command_base} {source_step_argument}"
                    )
                }
                if applicable_skills
                else {}
            ),
            "confirm_terminal_alias": (
                "omni game player --json --agent-brief skill confirm-terminal-alias "
                "<failed_skill_version_id> --environment <environment_id> "
                "--failed-run <run_id> --source-step <terminal_evidence_step_id> "
                '--source-state <source_state_id> --meaning "<为何仍满足目标>"'
            ),
            **(
                {
                    "guide_refresh_submit": (
                        "omni game player --json --agent-brief " + triggered_guide_refresh.command
                    )
                }
                if triggered_guide_refresh is not None
                else {}
            ),
            "bounds_semantics": "tap-preview 的点和 bounds 均直接使用 agent preview 像素；"
            "CLI 自动换算原图。bounds 接收左上 x、左上 y、宽度、高度四个空格分隔整数；"
            "禁止逗号，禁止把右下坐标当成宽高。",
            "read_only_command_semantics": (
                "observe capture 与 observe locate 不接受 --session；直接照模板执行。"
                "act swipe 只接收 x y x2 y2 四个位置参数，持续时间不是位置参数。"
            ),
            "element_locator_semantics": (
                "A2 自行选择语义目标；--expect 只描述本次动作应直接产生的下一层界面或"
                "可见变化，不把后续确认动作写进同一步预期。expectation_met=false 后先看"
                "after_preview：若已经进入新界面，新界面上的控件是新语义目标，可基于该"
                "权威预览继续使用 tap-preview；仅当画面未进入新状态、确需重试原语义目标时，"
                "下一次指针动作必须先运行 locate_elements，再把回执中的 locator_result.id "
                "与 element_id 原样填入 tap_element，禁止自行提供像素。tap_element 从定位结果"
                "自动绑定来源 EvidenceStep，不接受也不需要 --source-step。"
            ),
            "visual_distance_semantics": (
                "只改变等级、资源数或短文本的动作使用 0.001，并把 bounds 收紧到变化字段；"
                "局部控件或浮层变化使用 0.01；整页、面板或场景切换使用 0.03。"
            ),
        },
        "evaluation_contract": {
            "effect_scope": _AGENT_EFFECT_SCOPE,
            "semantic_success": "unverified",
            "rule": (
                "expectation_met 只证明声明区域达到视觉变化阈值；"
                "必须查看 after_preview 或后续状态后才能确认语义结果。"
            ),
        },
        "account_authorization": {
            "autonomous": (
                "全部正常游戏内行为，包括自主消耗虚拟货币、资源、体力和道具；"
                "按优秀玩家的长期账号收益决策。"
            ),
            "requires_separate_authorization": [
                "真实货币支付",
                "向游戏外服务提交个人身份资料",
            ],
        },
        "recalled_memory": [
            {
                "id": item.id,
                "kind": item.kind,
                "subject_id": item.subject_id,
                "payload": item.payload,
                "evidence_step_ids": [
                    step_id
                    for reference in item.evidence_refs
                    for step_id in reference.evidence_step_ids
                ],
            }
            for item in active_memories
        ],
        "incremental_instruction": instruction.strip() if instruction else None,
        "input_image_count": len(model_image_paths),
    }
    focus_receipt = _atomic_agent_receipt(
        context,
        category="focus",
        payload=focus_packet,
    )
    mark_focus_build_stage("focus_receipt")
    latest_step_id = next(
        (
            step_id
            for reference in reversed(canonical.last_evidence_refs)
            for step_id in reversed(reference.evidence_step_ids)
        ),
        None,
    )
    latest_step = (
        context.facility().store.get_evidence_step(latest_step_id) if latest_step_id else None
    )
    latest_evaluation = getattr(latest_step, "live_evaluation", None)
    focus_brief = {
        "schema": "game-observatory.ai-player.a2-focus-brief.v2",
        "environment_id": environment_id,
        "session_id": session_id,
        "phase": external.phase_id,
        "task_and_exit": _one_line_agent_summary(
            f"{task.title}；退出：{task.reason}",
            limit=240,
        ),
        "budget": {
            "actions": canonical.remaining_action_budget,
            "seconds": round(canonical.remaining_time_seconds, 3),
            "tokens": canonical.remaining_token_budget,
        },
        "current": {
            "image_index": 1,
            "evidence_step_id": current_source_step_id,
            "semantic_state_id": (
                current_state_assignment.state_id if current_state_assignment is not None else None
            ),
        },
        "applicable_skills": applicable_skill_briefs,
        "learned_routes": learned_route_briefs,
        "candidate_skills": candidate_skill_briefs,
        "direct_account_metrics": _direct_account_metric_focus(environment.game_id),
        "surface_anchor_actions": list(surface_anchor_actions),
        "locator_hints": [
            {
                "selector": item.get("selector"),
                "disposition": item.get("disposition"),
                **(
                    {"next_command": item.get("next_command")}
                    if item.get("tap_element_ready") is True
                    else {
                        "command": item.get("command"),
                        "next_command": item.get("next_command"),
                    }
                ),
                "tap_element_ready": item.get("tap_element_ready") is True,
            }
            for item in surface_locator_hints
        ],
        "guide_refresh": (
            triggered_guide_refresh.model_dump(mode="json")
            if triggered_guide_refresh is not None
            else None
        ),
        "latest_action": {
            "evidence_step_id": latest_step_id,
            "outcome": (
                {
                    "expectation_met": latest_evaluation.expectation_met,
                    "evaluation_source": latest_evaluation.evaluation_source,
                    "effect_scope": latest_evaluation.effect_scope,
                    "semantic_success": "unverified",
                    "global_effect": latest_evaluation.global_visual_distance,
                    "local_effect": (
                        latest_evaluation.target_effect.visual_distance
                        if latest_evaluation.target_effect is not None
                        else None
                    ),
                }
                if latest_evaluation is not None
                else None
            ),
        },
        "recent_actions": [
            {key: value for key, value in item.items() if key not in {"before", "after"}}
            for item in recent_actions
        ],
        "memory": [
            {
                "id": item.id,
                "kind": item.kind,
                "summary": _one_line_agent_summary(item.payload, limit=96),
            }
            for item in active_memories
        ],
        "prior_micro_stage": (
            {
                "summary": _one_line_agent_summary(
                    task_episodes[-1].payload.get("summary", ""),
                    limit=120,
                ),
                "detail": _one_line_agent_summary(
                    task_episodes[-1].payload.get("detail", ""),
                    limit=240,
                ),
                "evidence_step_ids": [
                    step_id
                    for reference in task_episodes[-1].evidence_refs
                    for step_id in reference.evidence_step_ids
                ],
            }
            if task_episodes
            else None
        ),
        "command_names": [
            "navigate",
            "observe capture --focus",
            "observe locate",
            "act tap-preview",
            "act tap-anchor",
            "act tap-element",
            "act launch",
            "act swipe",
            "act back",
            "act wait",
            "act text",
            "account metric-derive",
            *(["skill replay"] if applicable_skills or candidate_skills else []),
            *(["skill invalidate"] if applicable_skills or candidate_skills else []),
            "skill confirm-terminal-alias",
            *(["guide refresh submit"] if triggered_guide_refresh is not None else []),
        ],
        "locate_command": (
            "omni game player --json --agent-brief observe locate "
            f"--environment {environment_id} --source-step {current_source_step_id} "
            f"--timeout {_EXTERNAL_LOCATOR_TIMEOUT_FLOOR_SECONDS}"
        ),
        "metric_policy": (
            "只在任务相关的稳定数值于同一动作 Before/After 中均清晰可见，且已有固定 definition JSON 时调用；"
            "definition 必须来自 direct_account_metrics；必须给出两个局部数值框，禁止全屏 OCR、"
            "禁止仅为页面跳转提取、禁止从文字总结填数、禁止把带“万/亿”的近似显示登记为精确整数。"
            "格式：account metric-derive --environment <id> --source-step <terminal_step> "
            "--definition <json> --source screenshot-ocr --before-region <x y w h> "
            "--after-region <x y w h>。"
        ),
        "agent_cli_prefix": "omni game player --json --agent-brief",
        "launch_package": expected_package,
        "effect_scope": _AGENT_EFFECT_SCOPE,
        "semantic": "unverified",
        "account_authorization": (
            "正常游戏内行为及虚拟货币/资源消耗均自主；仅真实货币支付与向游戏外服务提交"
            "个人身份资料需另行授权。"
        ),
        "locate_cold_timeout_seconds": _EXTERNAL_LOCATOR_TIMEOUT_FLOOR_SECONDS,
        "increment": (_one_line_agent_summary(instruction, limit=180) if instruction else None),
        "full_focus_ref": focus_receipt["path"],
        "full_focus_sha256": focus_receipt["sha256"],
    }
    encoded_focus_brief = _encode_a2_focus_brief(focus_brief)
    mark_focus_build_stage("brief_encoding")
    context.runtime["explore_focus_build_timings_ms"] = {
        **focus_build_timings_ms,
        "total_before_provider": round(
            (time.monotonic() - focus_build_started) * 1000,
            3,
        ),
    }
    guide_refresh_prompt = (
        "guide_refresh 只在当前触发器已确认时出现；执行其中 command 一次即算提交，"
        "禁止等待或轮询研究 worker，后续只推进不依赖该结果的安全行为。\n"
        if triggered_guide_refresh is not None
        else ""
    )
    verbatim_incremental_instruction = _verbatim_incremental_instruction_prompt(instruction)
    prompt = (
        "A2 紧凑焦点 v2。第一张图是当前权威预览；自行选择语义目标和路线。你已加载"
        "统一 CLI 合同，本轮只用 command_names 中的命令，并保留全局 --agent-brief。"
        + verbatim_incremental_instruction
        + "memory 中 kind=failure_forbidden 的条目是跨会话硬约束；命中其触发条件时必须遵守，禁止用当前猜测覆盖已有反例。"
        + "每次确定下一项界面或操作目标后，第一条命令必须是 navigate <目标>；它只查询已沉淀的"
        "状态图和技能图，并用非模型固定动作执行整段路径。命中时禁止再逐屏识别和逐步点击；"
        "没有已知路径时会在触碰设备前失败，此后才允许探索缺失片段。"
        "navigate 无路径后，若 surface_anchor_actions 命中当前目标，必须原样执行其中 command："
        "fixed 锚点禁止自行换算像素；dynamic 锚点必须先在审阅区域内 locate，再按 next_command "
        "走 tap-element。未列出的锚点不得猜测或扩大定位区域。"
        "若 locator_hints 命中当前目标，优先使用 intent_alignment=aligned 的首项："
        "current_frame_locator_ready 直接原样执行 next_command；"
        "current_frame_visual_validation_required 只执行一次其限定区域 command，再把唯一匹配的"
        "locator_result_id 与 element_id 填入 next_command。它只复用跨状态控件共识，不复用历史"
        "坐标；禁止改成全屏定位或自行扩大区域。"
        "prior_micro_stage 是同一任务最近一次有来源的结论；规划前先使用它，资源或兵力条件"
        "尚未发生相关变化时禁止重走同一失败前置。"
        "动作回执直接给出下一张小预览、EvidenceStep、局部/全局效果和剩余预算；同一目标"
        "effect_scope=visual_state_change_only，expectation_met 不能单独证明复选框或业务语义成功，"
        "必须查看 after_preview 或后续状态。"
        "applicable_skills 只包含当前状态和环境可执行的 preferred 技能；任务目标与其终态一致时，"
        "优先用 skill replay 调用固定层。learned_routes 是至少成功执行过一次、当前起点可用的"
        "目标级固定路径；必须原样执行 route_command，通过 navigate 进入无模型固定层，禁止再次"
        "显式 skill replay 或逐步识别。candidate_skills 只包含从未成功运行、"
        "且当前起点适用的候选原子动作；多步路线由 navigate 在固定原子图中按需组合，"
        "未晋升的候选长流程不会直接回放。"
        "当任务下一目标与 terminal_state_ids 一致时，照其 trial_command 完成一次自然状态入口下的"
        "受控试跑。候选试跑用于独立验证，不代表 preferred 晋升，每轮最多一个；"
        "未列出的技能不得执行。"
        "固定技能回放后，若 after_preview 或后续终态截图清楚证明实际界面与目标语义不同，"
        "直接使用回执的 after_preview、terminal_evidence_step_id 与精确版本号执行 skill invalidate；"
        "只有回执缺图时才 observe capture --focus；"
        "禁止为取得版本号读取完整回执。"
        "navigate 若安全中断并返回 can_confirm_terminal_alias=true，先看同一回执中的 after_preview；"
        "只有实际终态仍满足本次目标、差异仅为来路或动态内容时，按回执字段调用一次 "
        "skill confirm-terminal-alias。确认后该技能、起点与终态组合会进入固定层；实际界面语义"
        "不同则不得确认。回执已有图时禁止补做 observe。"
        "不得停用未实际回放或仅因画面动态而暂时无法确认的技能。"
        "资源不足、部队或任务占用、冷却、解锁条件、次数限制以及确认或警告弹窗属于运行时"
        "前置失败；保留失败 SkillRun 和终态证据，禁止因此停用技能。"
        "调用 shell 工具执行 omni game player 的 act、observe 或 locate 命令时，将该工具调用的 "
        "timeout_ms 设为至少120000；普通交互通常60秒内返回，冷启动定位器保留90秒预算，"
        "不得被外层默认值提前终止。超过设施声明的预算仍未返回时才按超时恢复处理，禁止因此"
        "重放副作用未知的动作。observe capture 与 observe locate 不接受 --session；act swipe "
        "只接收四个坐标位置参数，不要追加持续时间位置参数。"
        + guide_refresh_prompt
        + "--expect 只写当前动作直接产生的下一层界面或可见变化，不跨越后续确认步骤。首次"
        "未达预期后先看 after_preview：已进入新界面时，把新控件当作新目标继续；画面未进入"
        "新状态且需要重试原目标时，先 observe locate（冷启动上限90秒，命中缓存立即返回），"
        "再按 element_id 操作。先从当前画面设定一个可闭合"
        "的微阶段，正常每个 provider invocation "
        "连续完成三至六个有意义动作。第一或第二个动作成功不是交回理由；每次动作后"
        "直接使用回执的 after_preview 与新 evidence_step_id 继续。新界面若清晰可辨且属于"
        "预期链路，仍在同一微阶段内，不停轮、不重做阶段规划。达到六个动作必须交回；已完成"
        "至少三个动作且微阶段闭合时可交回。只有界面真正陌生或歧义、预期失败、"
        "需重新语义规划的高影响不可逆决策、任务退出、设施错误、连续两次无增益或剩余预算"
        "不足时，才可在三个动作之前交回。已授权的普通游戏内资源消耗不属于高影响不可逆决策。需要完整"
        "incremental_instruction 已给出精确目标、source-step、命令边界或动作上限时，"
        "直接执行该约束，无需读取 full_focus_ref。只有焦点摘要确实缺少本轮决策所需的"
        "命令模板、记忆或证据时，才按 full_focus_ref+sha 读取一次；Windows PowerShell "
        "必须使用 Get-Content -Raw -Encoding UTF8 -LiteralPath <full_focus_ref>，禁止使用"
        "默认编码，禁止例行读取。\n"
        "若动作回执带 recovery_command，动作已经生效但原 EvidenceStep 不可继续引用；禁止重放，"
        "直接执行该只读命令刷新锚点。后续在同一持续动画界面使用 tap-preview 时必须加"
        " --dynamic-scene；swipe/back/wait/text 不支持该参数，严格按各自模板执行。\n"
        "wait 默认用于取得同页稳定的 Before/After，不要附加 --expect-change；只有明确等待"
        "加载或状态迁移时才可主动加 --expect-change 和匹配的最小视觉距离。\n"
        "仅改变等级、资源数或短文本时使用 --min-visual-distance 0.001，并把 bounds 收紧到"
        "变化字段；局部控件/浮层用0.01，整页或场景切换用0.03。该变化阈值规则不适用"
        "于稳定 wait；稳定 wait 必须省略 --min-visual-distance，使用模板默认容差。\n"
        "账号指标只在本轮动作的 Before/After 都清晰显示同一项任务相关稳定数值时提取；"
        "definition 只能从 direct_account_metrics 选择。用 account metric-derive 指定两个局部数值框，"
        "禁止全屏 OCR、禁止每步例行提取、禁止从模型总结填数，也禁止把带“万/亿”的近似显示"
        "登记为精确整数；若当前页只有缩写，应继续寻找游戏内详情页或把该指标明确留作缺口。\n"
        "When the incremental instruction explicitly caps a route or skill validation "
        "to one or two actions, finish that exact validation and report "
        "micro_stage_complete; never add unrelated actions merely to reach the normal "
        "three-action batch target. Such a short closed validation remains auditable.\n"
        "Only when the foreground state blocks every meaningful reachable task, do "
        "not poll again; include WAIT_SCOPE=foreground_blocking and WAIT_SECONDS=N "
        "immediately before the mandatory A2 turn report. Background recruitment, "
        "construction, recovery, or other parallel timers never justify scheduled "
        "wait while another task or system can be explored. Never use WAIT_SECONDS "
        "for low action/time budget; end normally so the canonical session can roll "
        "over.\n"
        "每次交回时，最终答复必须以下列三行结束；动作数填本 invocation 真实执行的动作数，"
        "摘要用一行中文说清完成了什么。停轮原因只允许 micro_stage_complete、action_cap、"
        "unfamiliar_or_ambiguous、expectation_failed、high_impact_irreversible、task_exit、"
        "facility_error、two_no_gain、budget_insufficient、foreground_blocking：\n"
        "A2_TURN_STOP_REASON=<reason>\n"
        "A2_ACTIONS_EXECUTED=<0..6>\n"
        "A2_MICRO_STAGE_SUMMARY=<一行中文摘要>\n"
        "焦点数据：\n" + encoded_focus_brief.decode("utf-8")
    )
    click_context.invoke(
        player_session_resume,
        session_id=session_id,
        prompt_text=prompt,
        prompt_file=None,
        image_paths=model_image_paths,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
    )


def _session_live_evidence_runs(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    session_id: str,
) -> dict[str, EvidenceRun]:
    """Return all recent action EvidenceRuns bound to one canonical session."""

    return {
        run.id: run
        for run in context.facility().store.list_evidence_runs(
            limit=1000,
            scope_id=environment_id,
            environment_id=environment_id,
            ai_player_session_id=session_id,
        )
        if run.scope_id == environment_id
        and run.environment.get("environment_id") == environment_id
        and run.environment.get("ai_player_session_id") == session_id
    }


def _recent_session_action_summaries(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    session_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return a compact, ordered action trail for A2 replanning and loop avoidance."""

    store = context.facility().store
    with store.read_session():
        runs = sorted(
            _session_live_evidence_runs(
                context,
                environment_id=environment_id,
                session_id=session_id,
            ).values(),
            key=lambda item: (item.started_at, item.id),
        )
        summaries: list[dict[str, Any]] = []
        for run in runs:
            expectation = str(run.environment.get("pre_execution_expectation") or "")
            for step_id in run.step_ids:
                step = store.get_evidence_step(step_id)
                if step is None or step.action_run_id is None:
                    continue
                evaluation = step.live_evaluation
                summaries.append(
                    {
                        "step": step.id,
                        "action": step.action.type,
                        "target": _one_line_agent_summary(
                            step.target_name or "未命名目标",
                            limit=64,
                        ),
                        "expect": _one_line_agent_summary(expectation, limit=80),
                        "visual_effect": (
                            evaluation.expectation_met if evaluation is not None else None
                        ),
                        "semantic_success": "unverified",
                        "before": step.before_frame_id,
                        "after": step.after_frame_id,
                    }
                )
    return summaries[-max(1, limit) :]


def _verbatim_incremental_instruction_prompt(instruction: str | None) -> str:
    if not instruction or not instruction.strip():
        return ""
    return (
        "本轮 incremental_instruction 完整原文如下；它是本轮最高优先级执行边界，"
        "无需通过 full_focus_ref 还原：\n"
        f"{instruction.strip()}\n"
    )


def _encode_a2_focus_brief(
    focus_brief: dict[str, Any],
    *,
    maximum_bytes: int = 3000,
) -> bytes:
    """Fit the repeated A2 turn brief without deleting canonical focus/history."""

    def encode() -> bytes:
        return json.dumps(
            focus_brief,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    encoded = encode()
    if len(encoded) > maximum_bytes and "command_names" in focus_brief:
        # The full signed facility contract already carries the exhaustive command
        # catalog. Preserve task history and failure memory in-band before repeated
        # command names, while retaining every immediate warm-path primitive.
        focus_brief["command_names"] = [
            name
            for name in focus_brief["command_names"]
            if name
            in {
                "navigate",
                "skill replay",
                "skill invalidate",
                "observe capture --focus",
                "observe locate",
                "act tap-preview",
                "act tap-anchor",
                "act tap-element",
                "account metric-derive",
            }
        ]
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("surface_anchor_actions"):
        focus_brief["surface_anchor_actions"] = [
            {
                key: value
                for key, value in item.items()
                if key in {"anchor_id", "disposition", "command", "next_command"}
            }
            for item in focus_brief["surface_anchor_actions"]
        ]
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("locator_hints"):
        focus_brief["locator_hints"] = [
            {
                "selector": item.get("selector"),
                "disposition": item.get("disposition"),
                **(
                    {"next_command": item.get("next_command")}
                    if item.get("tap_element_ready") is True
                    else {
                        "command": item.get("command"),
                        "next_command": item.get("next_command"),
                    }
                ),
                "tap_element_ready": item.get("tap_element_ready") is True,
            }
            for item in focus_brief["locator_hints"]
        ]
        encoded = encode()
    while len(encoded) > maximum_bytes and focus_brief["memory"]:
        removable_index = next(
            (
                index
                for index in range(len(focus_brief["memory"]) - 1, -1, -1)
                if focus_brief["memory"][index].get("kind") != "failure_forbidden"
            ),
            None,
        )
        if removable_index is None:
            break
        focus_brief["memory"].pop(removable_index)
        encoded = encode()
    while len(encoded) > maximum_bytes and len(focus_brief["recent_actions"]) > 1:
        focus_brief["recent_actions"].pop(0)
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief["recent_actions"]:
        focus_brief["recent_actions"].clear()
        encoded = encode()
    while len(encoded) > maximum_bytes and len(focus_brief.get("learned_routes", [])) > 1:
        focus_brief["learned_routes"].pop()
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("learned_routes"):
        focus_brief["learned_routes"] = [
            {
                key: value
                for key, value in focus_brief["learned_routes"][0].items()
                if key
                in {
                    "version_id",
                    "title",
                    "terminal_state_ids",
                    "successful_run_count",
                    "route_command",
                }
            }
        ]
        encoded = encode()
    while len(encoded) > maximum_bytes and len(focus_brief.get("candidate_skills", [])) > 1:
        focus_brief["candidate_skills"].pop()
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("candidate_skills"):
        focus_brief["candidate_skills"] = [
            {
                key: value
                for key, value in focus_brief["candidate_skills"][0].items()
                if key in {"version_id", "title", "terminal_state_ids", "trial_command"}
            }
        ]
        encoded = encode()
    if (
        len(encoded) > maximum_bytes
        and focus_brief.get("learned_routes")
        and focus_brief.get("candidate_skills")
    ):
        # A proven route outranks exploration of an unrelated virgin candidate.
        # The complete candidate list remains in ``full_focus_ref``.
        focus_brief["candidate_skills"] = []
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("prior_micro_stage"):
        prior = dict(focus_brief["prior_micro_stage"])
        prior.pop("evidence_step_ids", None)
        if prior.get("detail"):
            prior["detail"] = _one_line_agent_summary(prior["detail"], limit=120)
        focus_brief["prior_micro_stage"] = prior
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("applicable_skills"):
        focus_brief["applicable_skills"] = [
            {
                "version_id": item.get("version_id"),
                "title": _one_line_agent_summary(item.get("title", ""), limit=64),
                "actions": item.get("actions"),
                "terminal_state_ids": list(item.get("terminal_state_ids") or [])[:3],
            }
            for item in focus_brief["applicable_skills"][:2]
        ]
        encoded = encode()
    if len(encoded) > maximum_bytes:
        focus_brief["task_and_exit"] = _one_line_agent_summary(
            focus_brief["task_and_exit"],
            limit=120,
        )
        if focus_brief["increment"] is not None:
            focus_brief["increment"] = _one_line_agent_summary(
                focus_brief["increment"],
                limit=96,
            )
        encoded = encode()
    if len(encoded) > maximum_bytes:
        # Exact learned-route and virgin-candidate commands are the critical
        # warm-path payloads. Repeated policy prose and null/diagnostic fields
        # remain available via ``full_focus_ref`` and yield first.
        for key in (
            "metric_policy",
            "account_authorization",
            "latest_action",
            "guide_refresh",
            "effect_scope",
            "semantic",
            "locate_cold_timeout_seconds",
            "agent_cli_prefix",
            "launch_package",
        ):
            focus_brief.pop(key, None)
        if not focus_brief.get("applicable_skills"):
            focus_brief.pop("applicable_skills", None)
        encoded = encode()
    if len(encoded) > maximum_bytes and "command_names" in focus_brief:
        focus_brief["command_names"] = [
            name
            for name in focus_brief["command_names"]
            if name
            in {
                "navigate",
                "skill replay",
                "skill invalidate",
                "observe capture --focus",
                "observe locate",
                "act tap-preview",
                "act tap-anchor",
                "act tap-element",
                "account metric-derive",
            }
        ]
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("prior_micro_stage"):
        # Keep the prior conclusion in-band so an unchanged resource or troop
        # dead end is not retried. Its evidence and full narrative remain in
        # the canonical focus artifact referenced by this brief.
        focus_brief["prior_micro_stage"] = {
            "summary": _one_line_agent_summary(
                focus_brief["prior_micro_stage"].get("summary", ""),
                limit=96,
            )
        }
        encoded = encode()
    if len(encoded) > maximum_bytes and len(focus_brief.get("applicable_skills", [])) > 1:
        focus_brief["applicable_skills"] = focus_brief["applicable_skills"][:1]
        encoded = encode()
    if len(encoded) > maximum_bytes and (
        focus_brief.get("learned_routes")
        or focus_brief.get("candidate_skills")
        or focus_brief.get("surface_anchor_actions")
        or focus_brief.get("locator_hints")
    ):
        # Exact executable commands already carry environment, session, and
        # source step. Drop only their duplicated envelope fields before ever
        # sacrificing a learned-route command or a virgin-candidate trial.
        focus_brief.pop("environment_id", None)
        focus_brief.pop("session_id", None)
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("memory"):
        # Failure memories are cross-session guards, but their canonical IDs and
        # full payloads are already retained by ``full_focus_ref``.  Keeping every
        # long ID in the repeated hot brief can reject a provider turn before the
        # model is invoked once several guards accumulate.  Preserve every guard's
        # actionable summary first; only then shed the oldest summaries if the
        # complete in-band set still cannot fit.
        focus_brief["memory"] = [
            {
                "kind": item.get("kind"),
                "summary": _one_line_agent_summary(item.get("summary", ""), limit=64),
            }
            for item in focus_brief["memory"]
        ]
        encoded = encode()
    while len(encoded) > maximum_bytes and len(focus_brief.get("memory", [])) > 1:
        focus_brief["memory"].pop(0)
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("memory"):
        focus_brief["memory"][0]["summary"] = _one_line_agent_summary(
            focus_brief["memory"][0].get("summary", ""),
            limit=32,
        )
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("candidate_skills"):
        # Virgin candidates are optional exploration hints, not a reason to
        # reject the whole player turn.  Long environment/session/source-step
        # identifiers can make even one exact trial command exceed the repeated
        # hot-brief budget.  The complete candidates remain hash-locked in
        # ``full_focus_ref`` and can be loaded on demand; keep the current task,
        # safety memories, and any proven route in band instead.
        focus_brief["candidate_skills"] = []
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("increment") is not None:
        # The complete incremental instruction is appended verbatim to the
        # provider prompt immediately after this brief.  Keeping a second,
        # character-truncated copy here can still overflow the byte budget for
        # Chinese spectator instructions, even after every repeated collection
        # has been compacted.
        focus_brief.pop("increment", None)
        encoded = encode()
    if len(encoded) > maximum_bytes:
        focus_brief["task_and_exit"] = _one_line_agent_summary(
            focus_brief.get("task_and_exit", ""),
            limit=72,
        )
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("direct_account_metrics"):
        # The metric contract is repeated in ``metric_policy`` and the canonical
        # focus artifact.  Keep the exact definition root and file allowlist
        # in-band so metric derivation remains directly executable.
        focus_brief["direct_account_metrics"] = {
            key: value
            for key, value in focus_brief["direct_account_metrics"].items()
            if key in {"root", "files"}
        }
        encoded = encode()
    if len(encoded) > maximum_bytes:
        for key in (
            "candidate_skills",
            "surface_anchor_actions",
            "locator_hints",
            "recent_actions",
        ):
            if not focus_brief.get(key):
                focus_brief.pop(key, None)
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("locate_command"):
        # The exact locate template remains hash-locked in ``full_focus_ref``;
        # ``command_names`` and ``current`` retain the immediate facility
        # boundary in this repeated brief.
        focus_brief.pop("locate_command", None)
        encoded = encode()
    if len(encoded) > maximum_bytes and focus_brief.get("prior_micro_stage"):
        focus_brief["prior_micro_stage"] = {
            "summary": _one_line_agent_summary(
                focus_brief["prior_micro_stage"].get("summary", ""),
                limit=48,
            )
        }
        encoded = encode()
    if len(encoded) > maximum_bytes and (
        focus_brief.get("learned_routes") or focus_brief.get("candidate_skills")
    ):
        # An unusually long exact command must not prevent the provider from
        # starting. The canonical focus receipt still contains the complete
        # warm paths and is addressable by its hash-locked reference.
        focus_brief.pop("learned_routes", None)
        focus_brief.pop("candidate_skills", None)
        focus_brief["warm_paths_in_full_focus"] = True
        encoded = encode()
    if len(encoded) > maximum_bytes:
        # Last-resort repeated brief: retain the current evidence pointer,
        # executable metric definitions, hard failure memory, and canonical
        # focus receipt.  The full focus/history is never deleted.
        minimal_brief = {
            key: value
            for key, value in focus_brief.items()
            if key
            in {
                "schema",
                "phase",
                "budget",
                "current",
                "direct_account_metrics",
                "memory",
                "command_names",
                "full_focus_ref",
                "full_focus_sha256",
                "warm_paths_in_full_focus",
            }
        }
        minimal_brief["task_and_exit"] = _one_line_agent_summary(
            focus_brief.get("task_and_exit", ""),
            limit=48,
        )
        focus_brief.clear()
        focus_brief.update(minimal_brief)
        encoded = encode()
    if len(encoded) > maximum_bytes:
        raise click.ClickException(
            f"A2 minimal focus brief exceeded {maximum_bytes} bytes"
        )
    return encoded


def _latest_latched_explore_interrupt(
    control: AIPlayerSessionControl,
    environment_id: str,
    session_id: str,
) -> Any | None:
    """Return the latest explicit explore interrupt unless it was later resumed."""

    for event in reversed(control.list_events(environment_id, session_id)):
        if event.event_type in {"started", "resumed"}:
            return None
        if event.event_type == "paused":
            return event if ".explore-interrupt." in event.command_id else None
    return None


def _is_guarded_action_checkpoint(event: Any) -> bool:
    return event.event_type == "checkpointed" and ".act." in event.command_id


def _explore_turn_action_counts(
    *,
    action_event_count: int,
    external_before_count: int,
    external_after_count: int,
) -> tuple[int, int]:
    """Keep the turn-local event count separate from delayed ledger reconciliation."""

    return (
        action_event_count,
        max(0, external_after_count - external_before_count),
    )


def _persist_explore_turn_memory(
    context: _PlayerCLIContext,
    *,
    environment_id: str,
    session_id: str,
    task_id: str,
    invocation_sequence: int,
    last_message: str,
    turn_report: dict[str, Any],
    evidence: list[dict[str, Any]],
    fallback_evidence_step_id: str | None,
    action_count: int,
    information_gain_delta: int,
    durable_progress_delta: int,
    skill_run_ids: list[str],
) -> MemoryRecordV1 | None:
    """Persist one sourced external turn so later generations avoid known dead ends."""

    summary = str(turn_report.get("micro_stage_summary") or "").strip()
    if not summary:
        return None
    identity = hashlib.sha256(f"{session_id}:{invocation_sequence}".encode("utf-8")).hexdigest()[
        :24
    ]
    memory_id = f"memory.external-turn.{identity}"
    existing = context.player().get_memory(environment_id, memory_id)
    if existing is not None:
        return existing
    step_ids = list(
        dict.fromkeys(
            str(step_id)
            for run in evidence
            if run.get("status") == "passed"
            for step_id in run.get("evidence_step_ids", [])
            if isinstance(step_id, str) and step_id
        )
    )
    references: dict[str, EvidenceReferenceV1] = {}
    for step_id in step_ids:
        _environment, _run, _step, reference = _evidence_reference_from_step(
            context,
            environment_id=environment_id,
            step_id=step_id,
        )
        references.setdefault(reference.model_dump_json(by_alias=True), reference)
    if not references and fallback_evidence_step_id:
        _environment, _run, _step, reference = _evidence_reference_from_step(
            context,
            environment_id=environment_id,
            step_id=fallback_evidence_step_id,
        )
        references[reference.model_dump_json(by_alias=True)] = reference
    narrative = re.split(
        r"(?mi)^\s*A2_TURN_STOP_REASON=",
        last_message,
        maxsplit=1,
    )[0].strip()
    return context.player().append_memory(
        MemoryRecordV1(
            id=memory_id,
            environment_id=environment_id,
            evidence_refs=list(references.values()),
            kind="episodic",
            subject_id=f"explore-task:{task_id}",
            payload={
                "schema": "game-observatory.ai-player.external-turn-memory.v1",
                "summary": summary,
                "detail": narrative[:2000],
                "session_id": session_id,
                "invocation_sequence": invocation_sequence,
                "stop_reason": turn_report.get("stop_reason"),
                "report_status": turn_report.get("status"),
                "report_issues": list(turn_report.get("issues") or []),
                "declared_action_count": turn_report.get("declared_action_count"),
                "actual_action_count": turn_report.get("actual_action_count"),
                "action_count": action_count,
                "information_gain_delta": information_gain_delta,
                "durable_progress_delta": durable_progress_delta,
                "skill_run_ids": skill_run_ids,
            },
        )
    )


def _run_explore_driver_turn(
    click_context: click.Context,
    *,
    session_id: str,
    environment_id: str,
    task_id: str,
    instruction: str | None,
    timeout_seconds: float,
    cwd: Path,
) -> dict[str, Any]:
    """Invoke the existing explore path once and return monitoring deltas."""

    context = click_context.find_object(_PlayerCLIContext)
    if context is None:
        raise click.ClickException("缺少 AI 玩家 CLI 上下文。")
    player = context.player()
    control = AIPlayerSessionControl(player)
    ledger = _external_ledger(context)
    external_before = ledger.get_session(session_id)
    task_before = player.get_task(environment_id, task_id)
    events_before = control.list_events(environment_id, session_id)
    before_event_ids = {event.id for event in events_before}
    graph = SemanticStateGraph(player)
    active_state_ids_before = {
        state.id for state in graph.states(environment_id, include_candidates=True)
    }
    edge_outcomes_before = {edge.id: edge.outcome for edge in graph.edges(environment_id)}
    account_metric_ids_before = {
        item.id for item in player.list_account_metric_derivations(environment_id, limit=1000)
    }
    skill_version_ids_before = {item.id for item in player.list_skill_versions(environment_id)}
    skill_run_ids_before = {item.id for item in player.list_skill_runs(environment_id)}
    evidence_runs_before = _session_live_evidence_runs(
        context,
        environment_id=environment_id,
        session_id=session_id,
    )
    before_actions = external_before.semantic_action_count if external_before else 0
    context.runtime.pop("explore_current_step_id", None)
    context.runtime.pop("explore_focus_build_timings_ms", None)
    emissions: list[dict[str, Any]] = []
    previous_sink = context.runtime.get("emission_sink")
    previous_suppression = context.runtime.get("suppress_output")
    context.runtime["emission_sink"] = emissions
    context.runtime["suppress_output"] = True
    nested_exit_code: int | None = None
    nested_error: str | None = None
    started = time.monotonic()
    try:
        click_context.invoke(
            player_explore_run,
            session_id=session_id,
            environment_id=environment_id,
            task_id=task_id,
            instruction=instruction,
            image_paths=(),
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )
    except click.exceptions.Exit as exc:
        nested_exit_code = exc.exit_code
    except click.ClickException as exc:
        nested_error = str(exc)
    finally:
        if previous_sink is None:
            context.runtime.pop("emission_sink", None)
        else:
            context.runtime["emission_sink"] = previous_sink
        if previous_suppression is None:
            context.runtime.pop("suppress_output", None)
        else:
            context.runtime["suppress_output"] = previous_suppression
    elapsed = time.monotonic() - started
    current_source_step_id = context.runtime.pop("explore_current_step_id", None)
    focus_build_timings_ms = context.runtime.pop("explore_focus_build_timings_ms", None)
    external_after = ledger.get_session(session_id)
    task_after = player.get_task(environment_id, task_id)
    canonical_after = control.get_session(environment_id, session_id)
    events_after = control.list_events(environment_id, session_id)
    new_events = [event for event in events_after if event.id not in before_event_ids]
    action_events = [event for event in new_events if _is_guarded_action_checkpoint(event)]
    expected_effect_events = [
        event
        for event in action_events
        if event.reason.startswith(_ACTION_EXPECTATION_MET_REASON_PREFIX)
    ]
    active_state_ids_after = {
        state.id for state in graph.states(environment_id, include_candidates=True)
    }
    edge_outcomes_after = {edge.id: edge.outcome for edge in graph.edges(environment_id)}
    verified_outcomes = {
        "verified_transition",
        "verified_state_change",
        "verified_progress",
    }
    verified_semantic_edge_delta = sum(
        outcome in verified_outcomes and edge_outcomes_before.get(edge_id) != outcome
        for edge_id, outcome in edge_outcomes_after.items()
    )
    candidate_state_delta = len(active_state_ids_after - active_state_ids_before)
    deferred_edge_delta = sum(
        outcome == "deferred" and edge_id not in edge_outcomes_before
        for edge_id, outcome in edge_outcomes_after.items()
    )
    account_metric_ids_after = {
        item.id for item in player.list_account_metric_derivations(environment_id, limit=1000)
    }
    account_metric_delta = len(account_metric_ids_after - account_metric_ids_before)
    skill_version_ids_after = {item.id for item in player.list_skill_versions(environment_id)}
    skill_run_ids_after = {item.id for item in player.list_skill_runs(environment_id)}
    new_skill_version_ids = sorted(skill_version_ids_after - skill_version_ids_before)
    new_skill_run_ids = sorted(skill_run_ids_after - skill_run_ids_before)
    task_progress_delta = int(
        task_before is not None
        and task_before.status != "completed"
        and task_after is not None
        and task_after.status == "completed"
    )
    information_gain_delta = (
        candidate_state_delta
        + deferred_edge_delta
        + task_progress_delta
        + len(new_skill_version_ids)
    )
    evidence_runs_after = _session_live_evidence_runs(
        context,
        environment_id=environment_id,
        session_id=session_id,
    )
    new_run_ids = sorted(evidence_runs_after.keys() - evidence_runs_before.keys())
    evidence = []
    typed_live_evaluations = []
    for run_id in new_run_ids:
        run = evidence_runs_after.get(run_id)
        steps = (
            [
                step
                for step_id in run.step_ids
                if (step := context.facility().store.get_evidence_step(step_id)) is not None
            ]
            if run is not None
            else []
        )
        typed_live_evaluations.extend(
            step.live_evaluation for step in steps if step.live_evaluation is not None
        )
        evidence.append(
            {
                "evidence_run_id": run_id,
                "status": run.status if run is not None else "missing",
                "evidence_step_ids": list(run.step_ids) if run is not None else [],
            }
        )
    resume_payload = next(
        (
            item
            for item in reversed(emissions)
            if item.get("schema") == "game-observatory.ai-player.external-session-resume-result.v1"
        ),
        {},
    )
    action_quality_values = resume_payload.get("action_quality_samples") or []
    if not isinstance(action_quality_values, (list, tuple)):
        raise click.ClickException("external action-quality projection is not a list")
    confirmed_action_quality_delta = count_confirmed_external_action_effects(action_quality_values)
    invocation_value = resume_payload.get("invocation")
    invocation = (
        invocation_value.model_dump(mode="json", by_alias=True)
        if hasattr(invocation_value, "model_dump")
        else dict(invocation_value or {})
    )
    wait_seconds = None
    last_message = ""
    last_message_path = invocation.get("last_message_path")
    if isinstance(last_message_path, str) and last_message_path:
        candidate = (ledger.root / last_message_path).resolve()
        try:
            candidate.relative_to(ledger.root)
        except ValueError:
            candidate = Path()
        if candidate.is_file():
            last_message = candidate.read_text(encoding="utf-8", errors="replace")
            wait_seconds = _declared_wait_seconds(last_message)
    invocation_images = list(invocation.get("input_images") or [])
    input_preview_sha256 = (
        str(invocation_images[0].get("sha256") or "")
        if invocation_images and isinstance(invocation_images[0], dict)
        else ""
    )
    state_ingest_error = resume_payload.get("state_ingest_error")
    after_actions = external_after.semantic_action_count if external_after else before_actions
    attempted_action_delta, external_semantic_action_delta = _explore_turn_action_counts(
        action_event_count=len(action_events),
        external_before_count=before_actions,
        external_after_count=after_actions,
    )
    typed_expected_effect_delta = sum(
        evaluation.expectation_met for evaluation in typed_live_evaluations
    )
    expected_effect_delta = max(
        typed_expected_effect_delta,
        len(expected_effect_events),
    )
    durable_progress_delta = (
        verified_semantic_edge_delta + task_progress_delta + account_metric_delta
    )
    meaningful_action_delta = max(
        confirmed_action_quality_delta,
        durable_progress_delta,
    )
    usage_increment = (
        verified_external_usage_increment(ledger, invocation_value)
        if isinstance(invocation_value, ExternalAgentInvocationV1)
        else None
    )
    usage_efficiency = _provider_usage_efficiency(
        (usage_increment.model_dump(mode="json") if usage_increment is not None else None),
        action_count=attempted_action_delta,
    )
    turn_report = _parse_a2_turn_report(
        last_message,
        actual_action_count=attempted_action_delta,
    )
    turn_memory_id = None
    turn_memory_error = None
    invocation_sequence = invocation.get("sequence")
    if isinstance(invocation_sequence, int):
        try:
            turn_memory = _persist_explore_turn_memory(
                context,
                environment_id=environment_id,
                session_id=session_id,
                task_id=task_id,
                invocation_sequence=invocation_sequence,
                last_message=last_message,
                turn_report=turn_report,
                evidence=evidence,
                fallback_evidence_step_id=(
                    current_source_step_id if isinstance(current_source_step_id, str) else None
                ),
                action_count=attempted_action_delta,
                information_gain_delta=information_gain_delta,
                durable_progress_delta=durable_progress_delta,
                skill_run_ids=new_skill_run_ids,
            )
            turn_memory_id = turn_memory.id if turn_memory is not None else None
        except (click.ClickException, KeyError, OSError, TypeError, ValueError) as exc:
            # Canonical EvidenceSteps remain authoritative. Surface projection
            # failure without repeating a completed device action.
            turn_memory_error = str(exc)
    return {
        "duration_seconds": round(elapsed, 3),
        "focus_build_timings_ms": focus_build_timings_ms,
        "provider_duration_seconds": invocation.get("provider_duration_seconds"),
        "provider_status": invocation.get("status", "not_started"),
        "invocation_sequence": invocation.get("sequence"),
        "input_preview_sha256": input_preview_sha256 or None,
        "attempted_action_delta": attempted_action_delta,
        "external_semantic_action_delta": external_semantic_action_delta,
        "typed_expected_effect_delta": typed_expected_effect_delta,
        "expected_effect_delta": expected_effect_delta,
        "expected_effect_source": (
            "evidence_step.live_evaluation"
            if len(typed_live_evaluations) >= attempted_action_delta
            else "evidence_step.live_evaluation+session_event_reason_fallback"
            if typed_live_evaluations
            else "session_event_reason_fallback"
        ),
        "meaningful_action_delta": meaningful_action_delta,
        "confirmed_action_quality_delta": confirmed_action_quality_delta,
        "durable_progress_delta": durable_progress_delta,
        "information_gain_delta": information_gain_delta,
        "candidate_state_delta": candidate_state_delta,
        "deferred_edge_delta": deferred_edge_delta,
        "verified_semantic_edge_delta": verified_semantic_edge_delta,
        "semantic_state_change_delta": verified_semantic_edge_delta,
        "task_progress_delta": task_progress_delta,
        "account_metric_delta": account_metric_delta,
        "skill_candidate_delta": len(new_skill_version_ids),
        "skill_candidate_version_ids": new_skill_version_ids,
        "skill_replay_delta": len(new_skill_run_ids),
        "skill_run_ids": new_skill_run_ids,
        "semantic_action_delta": attempted_action_delta,
        "a2_turn_report": turn_report,
        "turn_memory_id": turn_memory_id,
        "turn_memory_error": turn_memory_error,
        "turn_efficiency": {
            "normal_action_min": 3,
            "action_cap": 6,
            "actual_action_count": attempted_action_delta,
            "expected_effect_count": expected_effect_delta,
            "normal_target_met": 3 <= attempted_action_delta <= 6,
            "short_closed_stage": bool(
                turn_report["stop_reason"] == "micro_stage_complete"
                and 1 <= attempted_action_delta < 3
            ),
            "auditable_report": turn_report["status"] == "complete",
            "provider_usage": usage_efficiency,
        },
        "new_evidence": evidence,
        "task_status": task_after.status if task_after is not None else "missing",
        "state_ingest_error": state_ingest_error,
        "nested_exit_code": nested_exit_code,
        "error": nested_error or invocation.get("error"),
        "wait_seconds": wait_seconds,
        "remaining_action_budget": canonical_after.remaining_action_budget,
    }


def _explore_provider_budget_precheck(
    control: AIPlayerSessionControl,
    *,
    environment_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Read the canonical action budget immediately before a provider turn.

    The check deliberately does not reserve an action: one provider invocation may
    execute several device actions, and those remain atomically reserved by the
    device path.  Running it between completed invocations preserves in-flight
    commit/recovery semantics while preventing a known-empty turn from starting.
    """

    session = control.get_session(environment_id, session_id)
    if session is None:
        return None
    remaining = session.remaining_action_budget
    return {
        "schema": "game-observatory.ai-player.provider-budget-precheck.v1",
        "environment_id": environment_id,
        "session_id": session_id,
        "canonical_session_version": session.version,
        "remaining_action_budget": remaining,
        "required_action_budget": 1,
        "can_invoke_provider": remaining >= 1,
    }


@player_explore.command("drive")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--task", "task_id", required=True)
@click.option("--instruction", default=None, help="整阶段约束；不用于逐步选择目标。")
@click.option("--max-turns", type=click.IntRange(1), default=20, show_default=True)
@click.option(
    "--overall-timeout",
    type=click.FloatRange(min=1),
    default=900,
    show_default=True,
)
@click.option(
    "--turn-timeout",
    type=click.FloatRange(min=1, max=900),
    default=300,
    show_default=True,
)
@click.option(
    "--cwd",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=Path("."),
)
@click.pass_context
def player_explore_drive(
    click_context: click.Context,
    session_id: str,
    environment_id: str,
    task_id: str,
    instruction: str | None,
    max_turns: int,
    overall_timeout: float,
    turn_timeout: float,
    cwd: Path,
) -> None:
    """一次启动，同一外部 Session 自主运行到阶段停止条件。"""

    context = click_context.find_object(_PlayerCLIContext)
    if context is None:
        raise click.ClickException("缺少 AI 玩家 CLI 上下文。")
    facility_contract_change = detect_runtime_facility_contract_change()
    if facility_contract_change is not None:
        _emit(
            context,
            {
                "schema": "game-observatory.ai-player.autonomous-stage-drive.v1",
                "session_id": session_id,
                "environment_id": environment_id,
                "task_id": task_id,
                "stop_reason": "facility_contract_change",
                "resume_after_seconds": None,
                "elapsed_seconds": 0.0,
                "turns": [],
                "facility_contract_change": facility_contract_change,
            },
            summary="连续探索在设施版本边界安全停止，可沿用原生 Session 恢复。",
        )
        return
    control = AIPlayerSessionControl(context.player())
    live_instruction_store = LiveInstructionStore(context.facility().store.root)
    completed_round_id = live_instruction_store.latest_open_round_id(
        environment_id=environment_id,
        session_id=session_id,
    )
    started = time.monotonic()
    turns: list[dict[str, Any]] = []
    consecutive_idle_turns = 0
    consecutive_ineffective_action_turns = 0
    consecutive_stalled_timeout_turns = 0
    consecutive_revisited_states = 0
    recent_state_fingerprints: list[str] = []
    stop_reason = "max_turns"
    stop_detail: dict[str, Any] | None = None
    for turn_index in range(1, max_turns + 1):
        delivered_spectator_instruction = None
        spectator_delivery_error = None
        if completed_round_id is not None:
            try:
                delivered_spectator_instruction = live_instruction_store.deliver_next(
                    environment_id=environment_id,
                    session_id=session_id,
                    after_round_id=completed_round_id,
                )
            except (OSError, sqlite3.Error, ValueError) as exc:
                spectator_delivery_error = str(exc)
        round_instruction = compose_round_instruction(
            instruction,
            delivered_spectator_instruction,
        )
        completed_round_id = None
        facility_contract_change = detect_runtime_facility_contract_change()
        if facility_contract_change is not None:
            stop_reason = "facility_contract_change"
            break
        if _latest_latched_explore_interrupt(control, environment_id, session_id):
            stop_reason = "externally_interrupted"
            break
        budget_precheck = _explore_provider_budget_precheck(
            control,
            environment_id=environment_id,
            session_id=session_id,
        )
        if budget_precheck is not None and not budget_precheck["can_invoke_provider"]:
            stop_reason = "budget_insufficient"
            stop_detail = {
                "reason": "budget_insufficient",
                "budget_precheck": budget_precheck,
                "next_turn": turn_index,
            }
            break
        remaining = overall_timeout - (time.monotonic() - started)
        if remaining < 1:
            stop_reason = "overall_timeout"
            break
        turn = _run_explore_driver_turn(
            click_context,
            session_id=session_id,
            environment_id=environment_id,
            task_id=task_id,
            instruction=round_instruction,
            timeout_seconds=min(turn_timeout, remaining),
            cwd=cwd,
        )
        turn["turn"] = turn_index
        turn["spectator_instruction_id"] = (
            delivered_spectator_instruction.id
            if delivered_spectator_instruction is not None
            else None
        )
        turn["spectator_instruction_delivery_error"] = spectator_delivery_error
        turns.append(turn)
        turn_report = turn.get("a2_turn_report") or {}
        if (
            turn.get("provider_status") == "succeeded"
            and turn_report.get("status") == "complete"
            and isinstance(turn.get("invocation_sequence"), int)
        ):
            completed_round_id = (
                f"{session_id}.provider-round.{turn['invocation_sequence']:04d}"
            )
            live_instruction_store.record_round_boundary(
                environment_id=environment_id,
                session_id=session_id,
                round_id=completed_round_id,
            )
        durable_progress = turn.get(
            "durable_progress_delta",
            turn["meaningful_action_delta"],
        )
        made_progress = bool(durable_progress > 0 or turn["information_gain_delta"] > 0)
        persisted_effect_progress = bool(
            turn.get("typed_expected_effect_delta", 0) > 0
            and any(
                evidence.get("status") == "passed" and evidence.get("evidence_step_ids")
                for evidence in turn.get("new_evidence", [])
            )
        )
        confirmed_turn_progress = made_progress or persisted_effect_progress
        new_evidence = turn.get("new_evidence", [])
        all_action_evidence_failed = bool(
            turn["attempted_action_delta"] > 0
            and new_evidence
            and all(evidence.get("status") != "passed" for evidence in new_evidence)
        )
        ineffective_action_turn = bool(
            turn["attempted_action_delta"] > 0
            and turn["provider_status"] != "timed_out"
            and turn["meaningful_action_delta"] == 0
            and (turn["expected_effect_delta"] == 0 or all_action_evidence_failed)
        )
        consecutive_ineffective_action_turns = (
            consecutive_ineffective_action_turns + 1 if ineffective_action_turn else 0
        )
        state_fingerprint = str(turn.get("input_preview_sha256") or "")
        revisited_state = bool(
            state_fingerprint
            and state_fingerprint in recent_state_fingerprints[-4:]
            and durable_progress == 0
        )
        consecutive_revisited_states = consecutive_revisited_states + 1 if revisited_state else 0
        if state_fingerprint:
            recent_state_fingerprints.append(state_fingerprint)
            recent_state_fingerprints = recent_state_fingerprints[-6:]
        consecutive_idle_turns = 0 if confirmed_turn_progress else consecutive_idle_turns + 1
        if _latest_latched_explore_interrupt(control, environment_id, session_id):
            stop_reason = "externally_interrupted"
            break
        if turn["task_status"] != "active":
            stop_reason = f"task_{turn['task_status']}"
            break
        a2_turn_report = turn.get("a2_turn_report") or {}
        if (
            a2_turn_report.get("status") == "complete"
            and a2_turn_report.get("stop_reason") == "task_exit"
        ):
            stop_reason = "provider_task_exit"
            break
        if (
            a2_turn_report.get("status") == "complete"
            and a2_turn_report.get("stop_reason") == "budget_insufficient"
        ):
            stop_reason = "provider_budget_insufficient"
            break
        if (
            a2_turn_report.get("status") == "complete"
            and a2_turn_report.get("stop_reason") == "expectation_failed"
            and turn["attempted_action_delta"] == 0
        ):
            # The provider has already made an explicit, evidence-backed refusal:
            # the requested known route cannot be proved from the live source.
            # Re-invoking it with the same task and unchanged screen only burns a
            # second model turn; stop here so the facility can repair recognition
            # or route coverage before resuming the same native session.
            stop_reason = "provider_expectation_failed_no_action"
            break
        wait_seconds = int(turn.get("wait_seconds") or 0)
        if wait_seconds:
            remaining_after_turn = overall_timeout - (time.monotonic() - started)
            if turn.get("remaining_action_budget", 0) <= 2:
                stop_reason = "canonical_budget_low"
                break
            if (
                wait_seconds > _MAX_IN_PROCESS_FOREGROUND_WAIT_SECONDS
                or remaining_after_turn <= wait_seconds + 1
            ):
                stop_reason = "scheduled_wait"
                break
            turn["in_process_wait_seconds"] = wait_seconds
            time.sleep(wait_seconds)
        if consecutive_ineffective_action_turns >= 2:
            stop_reason = "two_ineffective_action_turns"
            break
        if consecutive_revisited_states >= 2:
            stop_reason = "state_cycle"
            break
        provider_status = turn["provider_status"]
        recoverable_action_failure = bool(
            provider_status == "succeeded" and all_action_evidence_failed
        )
        if provider_status == "failed":
            stop_reason = "provider_failed"
            break
        if provider_status == "timed_out":
            if confirmed_turn_progress:
                consecutive_stalled_timeout_turns = 0
            else:
                consecutive_stalled_timeout_turns += 1
                if consecutive_stalled_timeout_turns >= 2:
                    stop_reason = "two_provider_timeouts"
                    break
        else:
            consecutive_stalled_timeout_turns = 0
        if turn["error"] and provider_status != "timed_out":
            stop_reason = "facility_error"
            break
        if (
            turn["state_ingest_error"]
            and not confirmed_turn_progress
            and not recoverable_action_failure
        ):
            stop_reason = "facility_error"
            break
        if (
            turn["nested_exit_code"]
            and provider_status != "timed_out"
            and not confirmed_turn_progress
            and not recoverable_action_failure
        ):
            stop_reason = "facility_error"
            break
        if consecutive_idle_turns >= 2:
            stop_reason = "two_idle_turns"
            break
    else:
        stop_reason = "max_turns"
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.autonomous-stage-drive.v1",
            "session_id": session_id,
            "environment_id": environment_id,
            "task_id": task_id,
            "stop_reason": stop_reason,
            "stop_detail": stop_detail,
            "resume_after_seconds": (
                turns[-1].get("wait_seconds") if turns and stop_reason == "scheduled_wait" else None
            ),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "turns": turns,
            "facility_contract_change": facility_contract_change,
        },
        summary=f"连续探索完成：{len(turns)} 轮，停止原因 {stop_reason}",
    )


def _explore_dispatch_observatory_root(context: _PlayerCLIContext) -> Path:
    """Resolve the dispatch ledger without initializing the game/database runtime."""

    return (context.root or default_observatory_root()).expanduser().resolve()


@player_explore.command("dispatch")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--task", "task_id", required=True)
@click.option("--instruction", default=None, help="Whole-stage guidance passed to drive.")
@click.option("--max-turns", type=click.IntRange(1), default=20, show_default=True)
@click.option(
    "--overall-timeout",
    type=click.FloatRange(min=1),
    default=900,
    show_default=True,
)
@click.option(
    "--turn-timeout",
    type=click.FloatRange(min=1, max=900),
    default=300,
    show_default=True,
)
@click.option(
    "--cwd",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=Path("."),
)
@click.pass_obj
def player_explore_dispatch(
    context: _PlayerCLIContext,
    session_id: str,
    environment_id: str,
    task_id: str,
    instruction: str | None,
    max_turns: int,
    overall_timeout: float,
    turn_timeout: float,
    cwd: Path,
) -> None:
    """Dispatch the unchanged explore drive into a detached supervised worker."""

    try:
        run = dispatch_drive(
            _explore_dispatch_observatory_root(context),
            session_id=session_id,
            environment_id=environment_id,
            task_id=task_id,
            instruction=instruction,
            max_turns=max_turns,
            overall_timeout=overall_timeout,
            turn_timeout=turn_timeout,
            cwd=cwd,
        )
    except ExploreDispatchError as exc:
        raise click.ClickException(str(exc)) from None
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.explore-dispatch-result.v1",
            "run": run,
        },
        summary=f"后台连续探索已托管：{run['run_id']}",
    )


@player_explore.command("dispatch-status")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--run-id", default=None, help="Inspect one run instead of the latest run.")
@click.pass_obj
def player_explore_dispatch_status(
    context: _PlayerCLIContext,
    session_id: str,
    environment_id: str,
    run_id: str | None,
) -> None:
    """Read detached-drive state without invoking the provider or game device."""

    ledger = ExploreDispatchLedger(_explore_dispatch_observatory_root(context))
    run = (
        ledger.status(run_id)
        if run_id is not None
        else ledger.latest_for_session(
            environment_id=environment_id,
            session_id=session_id,
        )
    )
    if run is None:
        raise click.ClickException("没有匹配的后台连续探索运行。")
    if run.get("session_id") != session_id or run.get("environment_id") != environment_id:
        raise click.ClickException("后台运行不属于指定 canonical session。")
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.explore-dispatch-status.v1",
            "run": run,
        },
        summary=f"后台连续探索：{run['state']} ({run['run_id']})",
    )


@player_explore.command("status")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.pass_context
def player_explore_status(
    click_context: click.Context,
    session_id: str,
    environment_id: str,
) -> None:
    """查看连续探索的 Session、任务、心跳、调用和最近证据。"""

    click_context.invoke(
        player_session_status,
        environment_id=environment_id,
        session_id=session_id,
        limit=100,
    )


@player_explore.command("interrupt")
@click.argument("session_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--reason", required=True)
@click.pass_obj
def player_explore_interrupt(
    context: _PlayerCLIContext,
    session_id: str,
    environment_id: str,
    reason: str,
) -> None:
    """暂停当前设备计划并保留同一个外部 provider Session。"""

    control = AIPlayerSessionControl(context.player())
    current = control.get_session(environment_id, session_id)
    if current is None:
        raise click.ClickException(f"Session 不存在: {session_id}")
    paused = current
    if current.state == "running":
        paused = control.pause(
            current.id,
            AIPlayerSessionCommand(
                command_id=f"{current.id}.explore-interrupt.{current.version}",
                environment_id=environment_id,
                expected_version=current.version,
                actor="ai-player-cli",
                reason=reason,
            ),
        )
    ledger = _external_ledger(context)
    external = ledger.get_session(session_id)
    if external is not None and external.status == "active":
        timestamp = utc_now()
        external = ledger.update_session(
            ExternalAgentContinuousSessionV1.model_validate(
                {
                    **external.model_dump(mode="json", by_alias=True),
                    "version": external.version + 1,
                    "status": "suspended",
                    "last_heartbeat_at": timestamp,
                    "updated_at": timestamp,
                }
            ),
            expected_version=external.version,
        )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.explore-interrupt-result.v1",
            "canonical_session": paused,
            "external_session": external,
        },
        summary=f"持续探索已暂停：{session_id}",
    )


@player_explore.command("resume")
@click.argument("session_id")
@click.option("--instruction", required=True)
@click.option("--timeout", "timeout_seconds", type=click.FloatRange(min=1), default=900)
@click.option(
    "--cwd",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    default=Path("."),
)
@click.pass_context
def player_explore_resume(
    click_context: click.Context,
    session_id: str,
    instruction: str,
    timeout_seconds: float,
    cwd: Path,
) -> None:
    """恢复同一个 provider Session；先重观测，再续做新一段。"""

    click_context.invoke(
        player_session_resume,
        session_id=session_id,
        prompt_text=instruction,
        prompt_file=None,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
    )


@cmd_player.group("state")
def player_state() -> None:
    """当前语义状态、状态地图和可靠路线。"""


@player_state.command("current")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_state_current(context: _PlayerCLIContext, environment_id: str) -> None:
    """查看当前状态及其来源；没有可靠识别时返回 null。"""

    player = context.player()
    state, basis = resolve_current_semantic_state(
        player,
        environment_id=environment_id,
    )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.current-state.v1",
            "state": (project_semantic_state(player, state) if state is not None else None),
            "basis": basis,
        },
        summary=f"当前状态：{state.id if state is not None else '未识别'}",
    )


@player_state.command("map")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_state_map(context: _PlayerCLIContext, environment_id: str) -> None:
    """列出界面/状态节点、已验证转移和当前节点。"""

    projection = build_ai_player_console_projection(context.player(), environment_id=environment_id)
    state_map = projection["state_map"]
    _emit(
        context,
        {"schema": "game-observatory.ai-player.state-map.v1", **state_map},
        summary=f"状态地图：{len(state_map['nodes'])} 节点 / {len(state_map['edges'])} 条边",
    )


@player_state.command("review-export")
@click.option("--environment", "environment_id", required=True)
@click.option(
    "--state",
    "state_ids",
    multiple=True,
    help="Current candidate state id to include; repeat for multiple states.",
)
@click.option(
    "--edge",
    "edge_ids",
    multiple=True,
    help="Current deferred edge id to include; repeat for multiple edges.",
)
@click.option(
    "--merge-target",
    "merge_target_ids",
    multiple=True,
    help="Current accepted state allowed as a read-only merge target; repeat as needed.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
)
@click.pass_obj
def player_state_review_export(
    context: _PlayerCLIContext,
    environment_id: str,
    state_ids: tuple[str, ...],
    edge_ids: tuple[str, ...],
    merge_target_ids: tuple[str, ...],
    output_path: Path,
) -> None:
    """导出候选状态、全部观察、待定转移和可读图像，供独立裁决。"""

    try:
        packet, packet_sha256 = export_state_review_packet(
            context.player(),
            environment_id,
            output_path,
            state_ids=state_ids,
            edge_ids=edge_ids,
            merge_target_ids=merge_target_ids,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.state-review-export-result.v1",
            "packet_id": packet.packet_id,
            "packet_path": str(output_path.resolve()),
            "packet_sha256": packet_sha256,
            "scope_mode": "scoped" if packet.scope is not None else "full_environment",
            "review_state_count": len(packet.states),
            "candidate_state_count": sum(
                item.state.status == "candidate" for item in packet.states
            ),
            "accepted_state_count": sum(
                item.state.status == "accepted" for item in packet.states
            ),
            "deferred_edge_count": len(packet.edges),
            "context_state_count": len(packet.context_states),
            "context_edge_count": len(packet.context_edges),
            "merge_target_state_count": (
                len(packet.scope.merge_target_state_ids) if packet.scope is not None else 0
            ),
        },
        summary=f"待裁决：{len(packet.states)} 个状态 / {len(packet.edges)} 条转移",
    )


@player_state.command("review-sign")
@click.option(
    "--seed",
    "seed_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
    help="独立审查者产出的未签名 StateAdjudicationSeedV1。",
)
@click.option(
    "--private-key",
    "private_key_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
    help="审查者进程持有的本地 Ed25519 私钥文件。",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
)
@click.pass_obj
def player_state_review_sign(
    context: _PlayerCLIContext,
    seed_path: Path,
    private_key_path: Path,
    output_path: Path,
) -> None:
    """由独立审查者密钥签署裁决；私钥内容不会进入 seed 或 canonical 数据库。"""

    try:
        seed, seed_sha256 = sign_state_adjudication_seed(
            seed_path,
            private_key_path,
            output_path,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.state-review-sign-result.v1",
            "seed_id": seed.seed_id,
            "reviewer_id": seed.adjudicator_id,
            "reviewer_session_id": seed.reviewer_session_id,
            "signed_seed_path": str(output_path.resolve()),
            "signed_seed_sha256": seed_sha256,
        },
        summary=f"裁决已签名：{seed.seed_id}",
    )


@player_state.command("review-apply")
@click.option(
    "--packet",
    "packet_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--seed",
    "seed_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--result",
    "result_path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
)
@click.option("--seed-sha256", "expected_seed_sha256", required=True)
@click.pass_obj
def player_state_review_apply(
    context: _PlayerCLIContext,
    packet_path: Path,
    seed_path: Path,
    result_path: Path,
    expected_seed_sha256: str,
) -> None:
    """应用 hash 锁定的独立裁决，晋升语义状态并闭合可靠转移。"""

    facility = context.facility()
    try:
        trust_path = StateAdjudicatorTrustStore.default_file(facility.store.root)
        if not trust_path.is_file():
            raise ValueError("state adjudicator trust root is not configured")
        result = apply_state_adjudication_seed(
            facility.store.root,
            packet_path,
            seed_path,
            result_path,
            expected_store_root=facility.store.root,
            expected_seed_sha256=expected_seed_sha256,
            trust_store=StateAdjudicatorTrustStore.from_file(trust_path),
        )
    except Exception as exc:  # noqa: BLE001 - normalize typed validation for CLI users
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.state-review-apply-result.v1",
            "result": result,
            "result_path": str(result_path.resolve()),
        },
        summary=(
            f"裁决已应用：{len(result.state_version_ids)} 个状态 / "
            f"{len(result.transition_version_ids)} 条转移"
        ),
    )


@player_state.command("route")
@click.option("--environment", "environment_id", required=True)
@click.option("--from", "start_state_id", required=True)
@click.option("--to", "goal_state_id", required=True)
@click.option("--max-actions", type=click.IntRange(0), default=None)
@click.pass_obj
def player_state_route(
    context: _PlayerCLIContext,
    environment_id: str,
    start_state_id: str,
    goal_state_id: str,
    max_actions: int | None,
) -> None:
    """只使用已验证转移，求到目标状态的最短路线。"""

    try:
        route = SemanticStateGraph(context.player()).shortest_verified_route(
            environment_id,
            start_state_id,
            goal_state_id,
            max_actions=max_actions,
        )
    except (KeyError, LookupError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {"schema": "game-observatory.ai-player.route-result.v1", "route": route},
        summary=f"可靠路线：{route.action_count} 个动作",
    )


@cmd_player.group("memory")
def player_memory() -> None:
    """带来源的工作、情节、语义、程序、任务和禁忌记忆。"""


@player_memory.command("recall")
@click.option("--environment", "environment_id", required=True)
@click.option("--kind", default=None, help="记忆种类。")
@click.option("--subject", "subject_id", default=None)
@click.option("--limit", type=click.IntRange(1, 500), default=100, show_default=True)
@click.pass_obj
def player_memory_recall(
    context: _PlayerCLIContext,
    environment_id: str,
    kind: str | None,
    subject_id: str | None,
    limit: int,
) -> None:
    """按环境、种类和主题召回，不跨账号偷带记忆。"""

    memories = context.player().list_memories(environment_id, subject_id=subject_id)
    if kind is not None:
        memories = [item for item in memories if item.kind == kind]
    memories = memories[-limit:]
    _emit(
        context,
        {"schema": "game-observatory.ai-player.memory-recall.v1", "memories": memories},
        summary=f"记忆：{len(memories)} 条",
    )


@player_memory.command("record")
@click.option("--environment", "environment_id", required=True)
@click.option(
    "--kind",
    type=click.Choice(
        [
            "identity_environment",
            "working",
            "episodic",
            "semantic",
            "procedural",
            "task",
            "failure_forbidden",
        ]
    ),
    required=True,
)
@click.option("--subject", "subject_id", required=True)
@click.option("--payload", "payload_text", default=None, help="一个 JSON 对象。")
@click.option(
    "--payload-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--id", "memory_id", default=None)
@click.pass_obj
def player_memory_record(
    context: _PlayerCLIContext,
    environment_id: str,
    kind: str,
    subject_id: str,
    payload_text: str | None,
    payload_file: Path | None,
    source_step_id: str,
    memory_id: str | None,
) -> None:
    """把一条带 canonical 来源的观察、规则、失败或任务记忆追加到账本。"""

    if bool(payload_text) == bool(payload_file):
        raise click.UsageError("--payload 与 --payload-file 必须且只能提供一个")
    if payload_file is not None:
        payload = _read_json_object(payload_file)
    else:
        try:
            payload = json.loads(str(payload_text))
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"--payload 不是有效 JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise click.UsageError("--payload 必须是 JSON 对象")
    _environment, _run, _step, reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=source_step_id,
    )
    memory = MemoryRecordV1(
        id=memory_id or f"memory.cli.{uuid.uuid4().hex}",
        environment_id=environment_id,
        evidence_refs=[reference],
        kind=kind,
        subject_id=subject_id,
        payload=payload,
    )
    try:
        stored = context.player().append_memory(memory)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {"schema": "game-observatory.ai-player.memory-record-result.v1", "memory": stored},
        summary=f"已记录记忆：{stored.id}",
    )


@player_memory.command("consolidate")
@click.option("--environment", "environment_id", required=True)
@click.option("--memory", "memory_ids", multiple=True, required=True)
@click.option("--summary", required=True, help="用连贯语句写明固化后的认识。")
@click.option("--id", "memory_id", default=None)
@click.pass_obj
def player_memory_consolidate(
    context: _PlayerCLIContext,
    environment_id: str,
    memory_ids: tuple[str, ...],
    summary: str,
    memory_id: str | None,
) -> None:
    """将同主题、同种类的多条来源记忆固化为一条可追溯后继。"""

    if len(memory_ids) < 2:
        raise click.UsageError("consolidate 至少需要两个 --memory")
    player = context.player()
    records = [player.get_memory(environment_id, item) for item in memory_ids]
    missing = [item for item, record in zip(memory_ids, records, strict=True) if record is None]
    if missing:
        raise click.ClickException(f"记忆不存在: {', '.join(missing)}")
    typed_records = [item for item in records if item is not None]
    if any(item.status != "active" for item in typed_records):
        raise click.ClickException("只能固化 active 记忆。")
    if (
        len({item.kind for item in typed_records}) != 1
        or len({item.subject_id for item in typed_records}) != 1
    ):
        raise click.ClickException("待固化记忆必须具有相同 kind 和 subject。")
    references: dict[str, EvidenceReferenceV1] = {}
    for record in typed_records:
        for reference in record.evidence_refs:
            references.setdefault(reference.model_dump_json(by_alias=True), reference)
    latest = max(typed_records, key=lambda item: (item.version, item.created_at, item.id))
    consolidated = MemoryRecordV1(
        id=memory_id or f"memory.cli.consolidated.{uuid.uuid4().hex}",
        version=max(item.version for item in typed_records) + 1,
        environment_id=environment_id,
        evidence_refs=list(references.values()),
        kind=latest.kind,
        subject_id=latest.subject_id,
        payload={
            "summary": summary,
            "consolidated_from_ids": list(memory_ids),
            "source_payloads": [item.payload for item in typed_records],
        },
        supersedes_id=latest.id,
    )
    try:
        stored = player.append_memory(consolidated)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.memory-consolidation-result.v1",
            "memory": stored,
            "source_memory_ids": list(memory_ids),
        },
        summary=f"已固化 {len(memory_ids)} 条记忆：{stored.id}",
    )


@cmd_player.group("skill")
def player_skill() -> None:
    """分层自动化技能、运行与验证。"""


@player_skill.command("list")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_skill_list(context: _PlayerCLIContext, environment_id: str) -> None:
    """列出技能版本及其适用环境。"""

    skills = context.player().list_skill_versions(environment_id)
    _emit(
        context,
        {"schema": "game-observatory.ai-player.skill-list.v1", "skills": skills},
        summary=f"技能：{len(skills)} 个版本",
    )


@player_skill.command("health")
@click.option("--environment", "environment_id", required=True)
@click.option("--detail-limit", type=click.IntRange(0, 40), default=10, show_default=True)
@click.pass_obj
def player_skill_health(
    context: _PlayerCLIContext,
    environment_id: str,
    detail_limit: int,
) -> None:
    """读取操作级复用健康、当前任务与可直接复用候选，不访问设备。"""

    started_at = time.perf_counter()
    try:
        payload = build_path_reuse_health_projection(
            context.player(),
            environment_id=environment_id,
            detail_limit=detail_limit,
        )
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload["projection_duration_ms"] = round(
        (time.perf_counter() - started_at) * 1000,
        1,
    )
    _emit(
        context,
        payload,
        summary=(
            f"复用健康：{payload['status']}；已热复用 "
            f"{payload['warm_reused_operation_count']}，单次成功 "
            f"{payload['single_success_operation_count']}，待二访 "
            f"{payload['pending_second_use_operation_count']}。"
        ),
    )


@player_skill.command("reconcile-operation-memory")
@click.option("--environment", "environment_id", required=True)
@click.option("--dry-run", is_flag=True)
@click.pass_obj
def player_skill_reconcile_operation_memory(
    context: _PlayerCLIContext,
    environment_id: str,
    dry_run: bool,
) -> None:
    """Idempotently project trusted single-action SkillRuns into OperationMemory."""

    try:
        receipt = OperationMemory(context.player()).reconcile_skill_history(
            environment_id,
            dry_run=dry_run,
        )
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        receipt.model_dump(mode="json", by_alias=True),
        summary=(
            f"operation memory: eligible={receipt.eligible_skill_run_count}, "
            f"inserted={receipt.inserted_observation_count + receipt.inserted_skill_execution_count}, "
            f"complete_contexts={receipt.complete_context_count}"
        ),
    )


@player_skill.command("show")
@click.argument("skill_id")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_skill_show(
    context: _PlayerCLIContext,
    skill_id: str,
    environment_id: str,
) -> None:
    """查看一个技能的版本、步骤、运行和验证。"""

    player = context.player()
    skills = [
        item
        for item in player.list_skill_versions(environment_id)
        if item.id == skill_id or item.skill_id == skill_id
    ]
    if not skills:
        raise click.ClickException(f"当前环境中没有这个技能: {skill_id}")
    version_ids = {item.id for item in skills}
    runs = [
        item
        for item in player.list_skill_runs(environment_id)
        if item.skill_version_id in version_ids
    ]
    validations = [
        item
        for item in player.list_skill_validations(environment_id)
        if item.skill_version_id in version_ids
    ]
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.skill-detail.v1",
            "versions": skills,
            "runs": runs,
            "validations": validations,
        },
        summary=f"技能 {skill_id}：{len(runs)} 次运行 / {len(validations)} 次验证",
    )


@player_skill.command("invalidate")
@click.argument("skill_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--expected-target", required=True)
@click.option("--observed-screen", required=True)
@click.pass_obj
def player_skill_invalidate(
    context: _PlayerCLIContext,
    skill_id: str,
    environment_id: str,
    source_step_id: str,
    expected_target: str,
    observed_screen: str,
) -> None:
    """用明确的终态语义反例停用错误技能，并保留全部历史与来源。"""

    if expected_target.strip() == observed_screen.strip():
        raise click.UsageError("--expected-target 与 --observed-screen 必须明确不同。")
    player = context.player()
    selected = player.get_skill_version_by_id(environment_id, skill_id)
    if selected is None:
        selected = player.get_skill_version(environment_id, skill_id)
    if selected is None:
        raise click.ClickException(f"当前环境中没有这个技能：{skill_id}")
    latest = player.get_skill_version(environment_id, selected.skill_id)
    if latest is None or latest.id != selected.id:
        raise click.ClickException("指定技能版本已经不是最新版本；未改动技能库，请重新读取该技能。")
    _environment, _run, _step, reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=source_step_id,
        require_semantic_source=False,
    )
    reason = (
        f"确定性回放的语义终态不匹配：技能“{selected.title}”预期到达"
        f"“{expected_target.strip()}”，终态证据显示“{observed_screen.strip()}”。"
    )
    try:
        invalidated = SkillLifecycle(player).degrade(
            environment_id,
            selected.skill_id,
            reason=reason,
            evidence_refs=[reference],
            invalidate=True,
        )
    except (KeyError, ValueError, SkillLifecycleError) as exc:
        raise click.ClickException(str(exc)) from exc
    result = {
        "schema": "game-observatory.ai-player.skill-invalidation-result.v1",
        "invalidated_skill": invalidated,
        "source_step_id": source_step_id,
        "expected_target": expected_target.strip(),
        "observed_screen": observed_screen.strip(),
    }
    if context.agent_brief:
        receipt = _atomic_agent_receipt(
            context,
            category="skill-invalidations",
            payload={"ok": True, **result},
        )
        brief = {
            "schema": "game-observatory.ai-player.skill-invalidation-brief.v1",
            "ok": True,
            "status": "invalidated",
            "skill_id": invalidated.skill_id,
            "skill_version_id": invalidated.id,
            "source_step_id": source_step_id,
            "next": "重新导航目标；固定层会绕开已失效版本，缺失片段再进入探索。",
            "full_receipt_ref": receipt["path"],
            "full_receipt_sha256": receipt["sha256"],
        }
        encoded = json.dumps(
            brief,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _SKILL_INVALIDATION_AGENT_BRIEF_MAX_BYTES:
            raise click.ClickException(
                "技能失效紧凑回执超出内部字节上限；完整结果已安全写入内容寻址回执。"
            )
        _emit_compact_json(context, brief)
        return
    _emit(
        context,
        result,
        summary=(f"已停用错误技能：{selected.title}；后续固定导航会绕开它并重新探索缺失路径。"),
    )


@player_skill.command("confirm-terminal-alias")
@click.argument("skill_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--failed-run", "failed_run_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--source-state", "source_state_id", default=None)
@click.option(
    "--meaning",
    required=True,
    help="语义检查结论，例如：返回后仍为城内主界面，只保留了地图选中状态。",
)
@click.pass_obj
def player_skill_confirm_terminal_alias(
    context: _PlayerCLIContext,
    skill_id: str,
    environment_id: str,
    failed_run_id: str,
    source_step_id: str,
    source_state_id: str | None,
    meaning: str,
) -> None:
    """确认一次安全中断的实际终态属于该已知路径的合法落点。"""

    player = context.player()
    skill = player.get_skill_version_by_id(environment_id, skill_id)
    if skill is None:
        raise click.ClickException(f"当前环境中没有这个技能版本：{skill_id}")
    if not _is_caller_dependent_skill(skill):
        raise click.ClickException("终态别名只用于返回、关闭或退出这类调用方相关操作。")
    failed_run = player.get_skill_run(environment_id, failed_run_id)
    if failed_run is None or failed_run.skill_version_id != skill.id:
        raise click.ClickException("指定失败运行不存在，或不属于该技能版本。")
    if failed_run.outcome == "success" or failed_run.action_count <= 0:
        raise click.ClickException("只能确认已经触发动作且被终态守卫中断的运行。")
    run_step_ids = {
        step_id for reference in failed_run.evidence_refs for step_id in reference.evidence_step_ids
    }
    if source_step_id not in run_step_ids:
        raise click.ClickException("终态 EvidenceStep 不属于指定的失败运行。")
    required_source_states = list(skill.applicability_scope.required_state_ids)
    if source_state_id is None:
        if len(required_source_states) != 1:
            raise click.UsageError("该技能有多个起点，必须显式提供 --source-state。")
        source_state_id = required_source_states[0]
    if source_state_id not in required_source_states:
        raise click.ClickException("来源状态不属于该技能声明的起点。")
    expected_terminal_state_id = _skill_terminal_state_id(skill)
    if expected_terminal_state_id is None:
        raise click.ClickException("该技能没有结构化终态，无法确认别名。")
    if len(meaning.strip()) < 4:
        raise click.UsageError("--meaning 需要写明为何实际画面仍满足这条路径的目标。")
    try:
        _environment, _run, _step, terminal_reference = _evidence_reference_from_step(
            context,
            environment_id=environment_id,
            step_id=source_step_id,
            require_semantic_source=False,
        )
        assignment = _ingest_and_resolve_evidence_state(
            context,
            environment_id=environment_id,
            evidence_refs=[terminal_reference],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if assignment is None:
        raise click.ClickException("终态证据尚未形成语义状态；没有写入程序记忆。")
    observed_terminal_state_id = assignment.state_id
    if observed_terminal_state_id == expected_terminal_state_id:
        raise click.ClickException("实际终态已经等于技能终态，不需要记录别名。")

    identity = hashlib.sha256(
        "\n".join(
            [
                skill.id,
                source_state_id,
                expected_terminal_state_id,
                observed_terminal_state_id,
            ]
        ).encode("utf-8")
    ).hexdigest()[:24]
    memory_id = f"memory.known-skill-terminal-alias.{identity}"
    existing = player.get_memory(environment_id, memory_id)
    if existing is None:
        existing = player.append_memory(
            MemoryRecordV1(
                id=memory_id,
                environment_id=environment_id,
                kind="procedural",
                subject_id=f"known-skill-terminal-alias:{skill.id}",
                payload={
                    "schema": ("game-observatory.ai-player.known-skill-terminal-alias.v1"),
                    "skill_version_id": skill.id,
                    "source_state_id": source_state_id,
                    "expected_terminal_state_id": expected_terminal_state_id,
                    "observed_terminal_state_id": observed_terminal_state_id,
                    "failed_run_id": failed_run.id,
                    "meaning": meaning.strip(),
                    "rule": (
                        "仅当同一技能、同一起点且终态截图再次匹配该状态时，"
                        "固定层可将其视为已确认落点。"
                    ),
                },
                evidence_refs=[terminal_reference],
            )
        )
    full_payload = {
        "schema": "game-observatory.ai-player.skill-terminal-alias-result.v1",
        "memory": existing,
        "skill_version_id": skill.id,
        "source_state_id": source_state_id,
        "expected_terminal_state_id": expected_terminal_state_id,
        "observed_terminal_state_id": observed_terminal_state_id,
    }
    if context.agent_brief:
        receipt = _atomic_agent_receipt(
            context,
            category="skill-terminal-aliases",
            payload=full_payload,
        )
        _emit_compact_json(
            context,
            {
                "schema": full_payload["schema"],
                "ok": True,
                "skill_version_id": skill.id,
                "source_state_id": source_state_id,
                "observed_terminal_state_id": observed_terminal_state_id,
                "next_replay_uses_fixed_guard": True,
                "receipt_ref": receipt["path"],
                "receipt_sha256": receipt["sha256"],
            },
        )
    else:
        _emit(
            context,
            full_payload,
            summary=(f"已记住 {skill.title} 的证据终态；下次由固定层直接校验和完成。"),
        )


@player_skill.command("replay")
@click.argument("skill_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--source-state-alias", "source_state_alias_id", default=None)
@click.option(
    "--allow-candidate",
    is_flag=True,
    help="仅用于独立验证；生产默认只运行 preferred 技能。",
)
@click.option("--visual-variant", "visual_variant_id", default=None)
@click.option("--independent-reset", "independent_reset_id", default=None)
@click.option("--baseline-model-input-tokens", type=click.IntRange(0), default=0)
@click.option("--baseline-decision-latency-ms", type=click.FloatRange(min=0), default=0.0)
@click.option(
    "--max-safety",
    type=click.Choice(
        ["read_only", "reversible", "progression", "social", "economic", "restricted"]
    ),
    default="economic",
    show_default=True,
)
@click.pass_obj
def player_skill_replay(
    context: _PlayerCLIContext,
    skill_id: str,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    source_state_alias_id: str | None,
    allow_candidate: bool,
    visual_variant_id: str | None,
    independent_reset_id: str | None,
    baseline_model_input_tokens: int,
    baseline_decision_latency_ms: float,
    max_safety: str,
) -> None:
    replay_started = time.perf_counter()
    phase_started = replay_started
    replay_timings = dict(context.runtime.get("known_takeover_timings_ms") or {})
    """通过 SkillRuntime 执行可客观核验的分层技能并保存签名 SkillRun。"""

    facility = context.facility()
    runtime_bundle = context.runtime.get("skill_runtime_bundle")
    if isinstance(runtime_bundle, dict) and runtime_bundle.get("store_root") == str(
        facility.store.root
    ):
        signer = runtime_bundle["signer"]
        player = runtime_bundle["player"]
    else:
        try:
            signer, trust_store = skill_runtime_signer_and_trust_store(facility.store.root)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        player = AIPlayerStore(
            facility.store,
            skill_validator_trust_store=trust_store,
        )
        context.runtime["skill_runtime_bundle"] = {
            "store_root": str(facility.store.root),
            "signer": signer,
            "player": player,
        }
    replay_timings["runtime_setup"] = round(
        (time.perf_counter() - phase_started) * 1000,
        3,
    )
    phase_started = time.perf_counter()
    skill = player.get_skill_version_by_id(environment_id, skill_id)
    if skill is None:
        skill = player.get_skill_version(environment_id, skill_id)
    if skill is None:
        raise click.ClickException(f"没有这个技能或版本: {skill_id}")
    replay_timings["skill_load"] = round(
        (time.perf_counter() - phase_started) * 1000,
        3,
    )
    phase_started = time.perf_counter()
    if allow_candidate and (independent_reset_id is None or visual_variant_id is None):
        raise click.UsageError(
            "候选技能独立验证必须显式提供 --independent-reset 和 --visual-variant；"
            "缺失基线按 0 留痕且不得用于效率晋级。"
        )
    try:
        cached_reference_binding = context.runtime.get("known_source_reference")
        cached_reference = (
            cached_reference_binding.get("reference")
            if isinstance(cached_reference_binding, dict)
            and cached_reference_binding.get("source_step_id") == source_step_id
            else None
        )
        if (
            isinstance(cached_reference, EvidenceReferenceV1)
            and cached_reference.environment_id == environment_id
            and source_step_id in cached_reference.evidence_step_ids
        ):
            initial_reference = cached_reference
        else:
            _environment, _source_run, _source_step, initial_reference = (
                _evidence_reference_from_step(
                    context,
                    environment_id=environment_id,
                    step_id=source_step_id,
                    require_semantic_source=False,
                )
            )
        cached_binding = context.runtime.get("known_source_assignment")
        cached_assignment = (
            cached_binding.get("assignment")
            if isinstance(cached_binding, dict)
            and cached_binding.get("source_step_id") == source_step_id
            else None
        )
        route_checkpoint = context.runtime.get("known_route_checkpoint")
        route_checkpoint_state_id = (
            str(route_checkpoint.get("state_id"))
            if isinstance(route_checkpoint, dict)
            and route_checkpoint.get("environment_id") == environment_id
            and route_checkpoint.get("source_step_id") == source_step_id
            and route_checkpoint.get("state_id")
            else None
        )
        if (
            cached_assignment is not None
            and getattr(cached_assignment, "environment_id", None) == environment_id
            and getattr(cached_assignment, "status", None) == "active"
        ):
            current_assignment = cached_assignment
        elif route_checkpoint_state_id is not None:
            # The prior known-route action already matched its declared endpoint.
            # Keep this as an in-memory route checkpoint; the final route action
            # ingests every pending EvidenceRun together and creates canonical
            # observations/assignments for all boundaries.
            current_assignment = None
        else:
            current_assignment = _ingest_and_resolve_evidence_state(
                context,
                environment_id=environment_id,
                evidence_refs=[initial_reference],
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if current_assignment is None and route_checkpoint_state_id is None:
        raise click.ClickException("来源 EvidenceStep 没有可核验的当前语义状态；设备未访问。")
    replay_state_id = (
        current_assignment.state_id if current_assignment is not None else route_checkpoint_state_id
    )
    source_state_alias_distance = None
    if source_state_alias_id is not None:
        if not allow_candidate:
            raise click.UsageError("来源状态别名只允许用于带独立验证证据的候选技能试跑。")
        if source_state_alias_id not in skill.applicability_scope.required_state_ids:
            raise click.ClickException("来源状态别名不属于该技能声明的起点。")
        first_action = next((item for item in skill.steps if item.kind == "action"), None)
        first_locator = next(
            (
                locator
                for locator in skill.locators
                if first_action is not None and locator.id == first_action.locator_id
            ),
            None,
        )
        known_aliases = context.runtime.get("verified_skill_entry_aliases")
        if (
            isinstance(known_aliases, set)
            and (
                skill.id,
                source_state_alias_id,
            )
            in known_aliases
        ):
            source_state_alias_distance = 0.0
        else:
            source_state_alias_distance = _source_step_state_visual_distance(
                context,
                environment_id=environment_id,
                source_step_id=source_step_id,
                state_id=source_state_alias_id,
                target_bounds=(
                    first_locator.reference_bounds if first_locator is not None else None
                ),
            )
        if source_state_alias_distance is None:
            raise click.ClickException("当前截图与技能来源状态不够接近，拒绝确定性别名回放。")
        replay_state_id = source_state_alias_id
    accepted_variants = skill.applicability_scope.visual_variant_ids
    resolved_visual_variant_id = visual_variant_id or "variant.unrestricted"
    if accepted_variants and visual_variant_id is None:
        raise click.ClickException(
            "技能限定视觉变体，必须由已验证分类结果显式提供 --visual-variant；设备未访问。"
        )

    if not allow_candidate and skill.validation_run_ids:
        validation_runs = [
            player.get_skill_run(environment_id, run_id) for run_id in skill.validation_run_ids
        ]
        available_runs = [run for run in validation_runs if run is not None]
        if baseline_model_input_tokens == 0 and available_runs:
            baseline_model_input_tokens = round(
                sum(run.baseline_model_input_tokens for run in available_runs) / len(available_runs)
            )
        if baseline_decision_latency_ms == 0 and available_runs:
            baseline_decision_latency_ms = sum(
                run.baseline_decision_latency_ms for run in available_runs
            ) / len(available_runs)

    replay_timings["source_resolution"] = round(
        (time.perf_counter() - phase_started) * 1000,
        3,
    )
    current_source_step_id = source_step_id
    # Preserve the canonical assignment for graph ingestion even when replay uses
    # a visually verified alias state for skill applicability.
    current_source_state_id = (
        current_assignment.state_id if current_assignment is not None else None
    )
    current_source_observation_id: str | None = (
        current_assignment.observation_id if current_assignment is not None else None
    )
    terminal_assignment: Any | None = None
    shared_pending_route_evidence_refs = context.runtime.get("known_route_pending_evidence_refs")
    pending_route_evidence_refs: list[EvidenceReferenceV1] = (
        shared_pending_route_evidence_refs
        if isinstance(shared_pending_route_evidence_refs, list)
        else []
    )
    defer_route_terminal_ingest = bool(context.runtime.get("defer_known_route_terminal_ingest"))
    shared_live_route_runtime = context.runtime.get("live_route_runtime")
    owns_live_route_runtime = not isinstance(
        shared_live_route_runtime,
        LiveStepRouteRuntime,
    )
    live_route_runtime = (
        LiveStepRouteRuntime.create(facility=facility, player=player)
        if owns_live_route_runtime
        else shared_live_route_runtime
    )
    action_step_ids = [item.id for item in skill.steps if item.kind == "action"]
    last_action_step_id = action_step_ids[-1] if action_step_ids else None
    latest_observation = GuardedSkillObservationReceiptV1(
        evidence_refs=[initial_reference],
        observed_state_id=replay_state_id,
        summary=f"来源证据绑定当前状态 {replay_state_id}",
    )

    def execute_guarded_skill_action(
        selected_skill: Any,
        step: Any,
        bounds: SourcePixelRect | None,
        _parameters: Any,
    ) -> GuardedSkillActionReceiptV1:
        nonlocal current_source_step_id
        nonlocal current_source_state_id
        nonlocal current_source_observation_id
        nonlocal latest_observation
        nonlocal terminal_assignment
        started = time.perf_counter()
        expected_after_state_id = next(
            (
                candidate.expected_state_id
                for candidate in selected_skill.steps
                if candidate.kind == "assert"
                and step.id in candidate.depends_on_step_ids
                and candidate.expected_state_id is not None
            ),
            None,
        )
        locator_started = time.perf_counter()
        resolved_action, resolved_bounds, locator_resolution = _resolve_skill_action_locator(
            context,
            skill=selected_skill,
            step=step,
            source_step_id=current_source_step_id,
        )
        replay_timings["locator_resolution"] = round(
            (time.perf_counter() - locator_started) * 1000,
            3,
        )
        guarded_action_started = time.perf_counter()
        result = _execute_guarded_action(
            context,
            environment_id=environment_id,
            session_id=session_id,
            source_step_id=current_source_step_id,
            action=resolved_action,
            target_name=f"{selected_skill.title} / {step.id}",
            target_bounds=resolved_bounds,
            expectation_summary=f"执行已验证技能“{selected_skill.title}”的 {step.id}",
            expect_change=step.action.type != "wait",
            min_visual_distance=0.001 if step.action.type != "wait" else 0.0,
            fast_known_route=True,
            source_semantic_state_id=current_source_state_id,
            source_state_observation_id=current_source_observation_id,
            expected_semantic_state_id=expected_after_state_id,
            skill_replay_version_id=selected_skill.id,
            locator_resolution=locator_resolution,
            dynamic_scene=locator_resolution is not None,
            route_runtime=live_route_runtime,
        )
        replay_timings["guarded_action"] = round(
            (time.perf_counter() - guarded_action_started) * 1000,
            3,
        )
        current_source_step_id = str(result["evidence_step_id"])
        # The next action's source is the prior After artifact.  It is carried by
        # the ordered batch map during graph ingest and has no persisted
        # observation assignment yet, so do not manufacture a half binding.
        current_source_state_id = None
        current_source_observation_id = None
        terminal_reference = result["canonical_session"].last_evidence_refs[-1]
        pending_route_evidence_refs.append(terminal_reference)
        observed_state_id = None
        summary = "守卫动作已返回终态证据"
        if expected_after_state_id is not None:
            terminal_match_started = time.perf_counter()
            remembered_terminal_aliases = sorted(
                KnownRouteProgram(player).remembered_skill_terminal_aliases(
                    environment_id,
                    selected_skill.id,
                    replay_state_id,
                )
            )
            matched_terminal_state_id: str | None = None
            visual_distance: float | None = None
            for candidate_terminal_state_id in [
                expected_after_state_id,
                *remembered_terminal_aliases,
            ]:
                candidate_distance = _source_step_state_visual_distance(
                    context,
                    environment_id=environment_id,
                    source_step_id=current_source_step_id,
                    state_id=candidate_terminal_state_id,
                    target_bounds=resolved_bounds,
                    allow_layout_variant=True,
                )
                if candidate_distance is None:
                    continue
                matched_terminal_state_id = candidate_terminal_state_id
                visual_distance = candidate_distance
                break
            replay_timings["terminal_visual_match"] = round(
                (time.perf_counter() - terminal_match_started) * 1000,
                3,
            )
            if visual_distance is None:
                try:
                    mismatch_assignment = _ingest_and_resolve_evidence_state(
                        context,
                        environment_id=environment_id,
                        evidence_refs=list(pending_route_evidence_refs),
                    )
                except Exception:  # noqa: BLE001 - keep the primary mismatch
                    mismatch_assignment = None
                terminal_assignment = mismatch_assignment
                pending_route_evidence_refs.clear()
                observed_mismatch_state_id = (
                    mismatch_assignment.state_id if mismatch_assignment is not None else None
                )
                latest_observation = GuardedSkillObservationReceiptV1(
                    evidence_refs=[terminal_reference],
                    observed_state_id=observed_mismatch_state_id,
                    summary=(
                        "固定路线终态没有匹配已记录界面，已停止后续动作；"
                        f"实际状态 {observed_mismatch_state_id or '未识别'}"
                    ),
                )
                return GuardedSkillActionReceiptV1(
                    ok=False,
                    evidence_refs=[terminal_reference],
                    observed_state_id=observed_mismatch_state_id,
                    decision_latency_ms=(time.perf_counter() - started) * 1000,
                    summary=latest_observation.summary,
                )
            if step.id != last_action_step_id or defer_route_terminal_ingest:
                if step.id == last_action_step_id:
                    context.runtime["known_route_checkpoint"] = {
                        "environment_id": environment_id,
                        "source_step_id": current_source_step_id,
                        "state_id": expected_after_state_id,
                    }
                latest_observation = GuardedSkillObservationReceiptV1(
                    evidence_refs=[terminal_reference],
                    observed_state_id=expected_after_state_id,
                    verified_state_guard=True,
                    summary=(
                        f"固定路线轻量校验通过：{matched_terminal_state_id} "
                        f"(visual_distance={visual_distance:.6f})"
                    ),
                )
                return GuardedSkillActionReceiptV1(
                    ok=bool(result["ok"]),
                    evidence_refs=[terminal_reference],
                    observed_state_id=expected_after_state_id,
                    decision_latency_ms=(time.perf_counter() - started) * 1000,
                    summary=latest_observation.summary,
                )
        try:
            terminal_ingest_started = time.perf_counter()
            assignment = _ingest_and_resolve_evidence_state(
                context,
                environment_id=environment_id,
                evidence_refs=list(pending_route_evidence_refs),
            )
            pending_route_evidence_refs.clear()
            if (
                assignment is not None
                and expected_after_state_id is not None
                and visual_distance is not None
                and assignment.state_id != expected_after_state_id
            ):
                observation = player.get_state_observation(
                    environment_id,
                    assignment.observation_id,
                )
                if observation is None:
                    raise ValueError(
                        "verified terminal assignment is missing its state observation"
                    )
                SemanticStateRecognizer(player).recognize_from_verified_state_guard(
                    observation,
                    expected_after_state_id,
                    method="expected_state_guard",
                )
                assignment = player.get_current_state_assignment(
                    environment_id,
                    observation.id,
                )
            replay_timings["terminal_state_ingest"] = round(
                (time.perf_counter() - terminal_ingest_started) * 1000,
                3,
            )
            terminal_assignment = assignment
            # A visually matched terminal prototype is the skill contract.  The
            # state inducer may mint a fresh state ID for account-content variants
            # (for example the same hero list after obtaining one more hero).  Do
            # not make deterministic replay fail after its visual guard passed;
            # retain the newly induced assignment for graph evidence while the
            # runtime assertion receives the canonical expected endpoint.
            observed_state_id = (
                expected_after_state_id
                if expected_after_state_id is not None and visual_distance is not None
                else assignment.state_id
                if assignment is not None
                else None
            )
            summary = (
                f"守卫动作终态绑定状态 {observed_state_id}"
                if observed_state_id is not None
                else "守卫动作终态暂未形成语义状态绑定"
            )
        except Exception as exc:  # noqa: BLE001 - preserve side-effect evidence
            replay_timings["terminal_state_ingest"] = round(
                (time.perf_counter() - terminal_ingest_started) * 1000,
                3,
            )
            summary = f"守卫动作已生效，终态状态归并失败：{type(exc).__name__}"
        latest_observation = _terminal_observation_receipt(
            terminal_reference=terminal_reference,
            observed_state_id=observed_state_id,
            expected_state_id=expected_after_state_id,
            visual_distance=visual_distance,
            summary=summary,
        )
        return GuardedSkillActionReceiptV1(
            ok=bool(result["ok"]),
            evidence_refs=[terminal_reference],
            observed_state_id=observed_state_id,
            decision_latency_ms=(time.perf_counter() - started) * 1000,
            summary=summary,
        )

    def observe_guarded_skill_state(
        _selected_skill: Any,
        _step: Any,
        _parameters: Any,
    ) -> GuardedSkillObservationReceiptV1:
        return latest_observation

    adapter = GuardedSkillStepAdapter(
        execute_guarded_action=execute_guarded_skill_action,
        observe_state=observe_guarded_skill_state,
    )
    runtime = SkillRuntime(
        player,
        adapter,
        run_signer=signer,
        timing_sink=replay_timings,
        defer_lifecycle_reconcile=bool(context.runtime.get("defer_skill_lifecycle_reconcile")),
    )
    previous_runs = player.list_skill_runs(
        environment_id,
        skill_version_id=skill.id,
    )
    request = SkillExecutionRequestV1(
        environment_id=environment_id,
        skill_id=skill.skill_id,
        skill_version_id=skill.id,
        validation_mode=allow_candidate,
        run_id=f"skill-run.cli.{uuid.uuid4().hex}",
        validator_id=signer.validator_id,
        attempt_index=len(previous_runs) + 1,
        independent_reset_id=(
            independent_reset_id or f"production.{session_id}.{uuid.uuid4().hex[:12]}"
        ),
        visual_variant_id=resolved_visual_variant_id,
        current_state_id=replay_state_id,
        initial_evidence_refs=[initial_reference],
        max_safety_level=max_safety,
        baseline_model_input_tokens=baseline_model_input_tokens,
        baseline_decision_latency_ms=baseline_decision_latency_ms,
    )
    try:
        phase_started = time.perf_counter()
        execution = runtime.execute(request)
    except (KeyError, TypeError, ValueError, SkillLifecycleError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if owns_live_route_runtime:
            live_route_runtime.close()
    replay_timings["skill_execution"] = round(
        (time.perf_counter() - phase_started) * 1000,
        3,
    )
    replay_timings["replay_total_before_receipt"] = round(
        (time.perf_counter() - replay_started) * 1000,
        3,
    )

    observed_entry_state_id = (
        current_assignment.state_id if current_assignment is not None else route_checkpoint_state_id
    )
    if (
        execution.run.outcome == "success"
        and observed_entry_state_id is not None
        and observed_entry_state_id != replay_state_id
        and source_state_alias_distance is not None
        and (
            execution.skill.id,
            replay_state_id,
        )
        not in KnownRouteProgram(player).remembered_skill_entry_aliases(
            environment_id,
            observed_entry_state_id,
        )
    ):
        alias_identity = hashlib.sha256(
            "\n".join(
                [
                    execution.skill.id,
                    observed_entry_state_id,
                    replay_state_id,
                ]
            ).encode("utf-8")
        ).hexdigest()[:24]
        alias_memory = MemoryRecordV1(
            id=f"memory.known-skill-entry-alias.{alias_identity}",
            environment_id=environment_id,
            kind="procedural",
            subject_id=f"known-skill-entry-alias:{execution.skill.id}",
            payload={
                "schema": "game-observatory.ai-player.known-skill-entry-alias.v1",
                "skill_version_id": execution.skill.id,
                "observed_state_id": observed_entry_state_id,
                "required_state_id": replay_state_id,
                "successful_run_id": execution.run.id,
                "visual_distance": source_state_alias_distance,
                "requires_settled_run": True,
                "rule": "该已识别界面可直接进入这条已学习固定路径。",
            },
            evidence_refs=[initial_reference, *execution.run.evidence_refs[-1:]],
        )
        if player.get_memory(environment_id, alias_memory.id) is None:
            player.append_memory(alias_memory)
    goal_alias_request = context.runtime.get(_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY)
    if (
        isinstance(goal_alias_request, dict)
        and goal_alias_request.get("skill_version_id") == execution.skill.id
        and isinstance(goal_alias_request.get("goal_alias"), str)
        and isinstance(goal_alias_request.get("provenance"), str)
    ):
        _remember_successful_skill_goal_alias(
            player,
            environment_id=environment_id,
            skill=execution.skill,
            run=execution.run,
            goal_alias=str(goal_alias_request["goal_alias"]),
            provenance=str(goal_alias_request["provenance"]),
            evidence_refs=[initial_reference, *execution.run.evidence_refs[-1:]],
        )
    if execution.run.outcome == "success" and terminal_assignment is not None:
        context.runtime["known_source_assignment"] = {
            "source_step_id": current_source_step_id,
            "assignment": terminal_assignment,
        }
        context.runtime.pop("known_route_checkpoint", None)

    navigation_stack = player.get_navigation_stack(environment_id, session_id)
    if execution.run.outcome == "success" and execution.run.objective_success:
        navigation_started = time.perf_counter()
        terminal_state_id = _skill_terminal_state_id(execution.skill)
        navigation_caller_state_id = (
            current_assignment.state_id if current_assignment is not None else replay_state_id
        )
        navigation_terminal_state_id = (
            terminal_assignment.state_id if terminal_assignment is not None else terminal_state_id
        )
        if terminal_state_id is not None and _is_caller_dependent_skill(execution.skill):
            frame = (
                navigation_stack.frames[-1]
                if navigation_stack is not None and navigation_stack.frames
                else None
            )
            if (
                frame is not None
                and navigation_stack is not None
                and navigation_stack.current_state_id == frame.entered_state_id
                and (
                    frame.return_skill_version_id == execution.skill.id
                    or (
                        frame.entered_state_id == replay_state_id
                        and frame.caller_state_id == terminal_state_id
                    )
                )
            ):
                navigation_stack = player.pop_navigation_frame(
                    environment_id,
                    session_id,
                    entered_state_id=replay_state_id,
                    caller_state_id=terminal_state_id,
                )
        elif (
            navigation_terminal_state_id is not None
            and navigation_terminal_state_id != navigation_caller_state_id
        ):
            return_skill_version_id = _learned_reverse_navigation_skill_id(
                player,
                environment_id,
                caller_state_id=navigation_caller_state_id,
                entered_state_id=navigation_terminal_state_id,
            )
            if return_skill_version_id is not None:
                navigation_stack = player.push_navigation_frame(
                    environment_id,
                    session_id,
                    NavigationFrameV1(
                        caller_state_id=navigation_caller_state_id,
                        entered_state_id=navigation_terminal_state_id,
                        forward_skill_version_id=execution.skill.id,
                        return_skill_version_id=return_skill_version_id,
                        source_evidence_step_id=source_step_id,
                        terminal_evidence_step_id=current_source_step_id,
                    ),
                )
        replay_timings["navigation_context"] = round(
            (time.perf_counter() - navigation_started) * 1000,
            3,
        )

    full_payload = {
        "schema": "game-observatory.ai-player.skill-replay-result.v2",
        "skill": execution.skill,
        "run": execution.run,
        "steps": execution.step_results,
        "terminal_evidence_step_id": current_source_step_id,
        "navigation_stack": navigation_stack,
        "timings_ms": replay_timings,
    }
    if context.agent_brief:
        receipt = _atomic_agent_receipt(
            context,
            category="skill-runs",
            payload={"ok": execution.run.outcome == "success", **full_payload},
        )
        current_session = AIPlayerSessionControl(player).get_session(
            environment_id,
            session_id,
        )
        terminal_step = context.facility().store.get_evidence_step(current_source_step_id)
        terminal_support = _action_agent_support(
            context,
            {
                "evidence_step_id": current_source_step_id,
                "after_artifact_id": (
                    terminal_step.after_frame_id if terminal_step is not None else None
                ),
            },
        )
        brief = {
            "schema": "game-observatory.ai-player.skill-replay-brief.v1",
            "ok": execution.run.outcome == "success",
            "skill_version_id": execution.skill.id,
            "run_id": execution.run.id,
            "outcome": execution.run.outcome,
            "objective_success": execution.run.objective_success,
            "actions": execution.run.action_count,
            "model_input_tokens": execution.run.model_input_tokens,
            "terminal_state_id": latest_observation.observed_state_id,
            "terminal_evidence_step_id": current_source_step_id,
            "after_preview": terminal_support["after_preview"],
            "remaining_actions": (
                current_session.remaining_action_budget if current_session is not None else None
            ),
            "navigation_stack_version": (
                navigation_stack.version if navigation_stack is not None else None
            ),
            "timings_ms": {
                "preflight": replay_timings.get("takeover_preflight_total"),
                "action": replay_timings.get("guarded_action"),
                "total": replay_timings.get("replay_total_before_receipt"),
            },
            "receipt_ref": receipt["path"],
            "receipt_sha256": receipt["sha256"],
        }
        encoded = json.dumps(brief, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 1200:
            raise click.ClickException(
                f"Agent skill brief exceeded 1200 bytes ({len(encoded)} bytes)"
            )
        _emit_compact_json(context, brief)
    else:
        _emit(
            context,
            full_payload,
            summary=f"技能重放 {execution.skill.id}：{execution.run.outcome}",
        )
    if execution.run.outcome != "success":
        raise click.exceptions.Exit(1)


@cmd_player.command("navigate")
@click.argument("goal")
@click.option("--environment", "environment_id", required=True)
@click.option("--session", "session_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option("--max-skills", type=click.IntRange(1, 20), default=12, show_default=True)
@click.option(
    "--max-safety",
    type=click.Choice(
        ["read_only", "reversible", "progression", "social", "economic", "restricted"]
    ),
    default="economic",
    show_default=True,
)
@click.pass_obj
def player_navigate_known_route(
    context: _PlayerCLIContext,
    goal: str,
    environment_id: str,
    session_id: str,
    source_step_id: str,
    max_skills: int,
    max_safety: str,
) -> None:
    """按目标自动选择并执行已学习路径；未命中时不触碰设备。"""

    if not context.runtime.get("session_runtime_host"):
        try:
            forwarded = forward_navigate_to_session_runtime(
                environment_id=environment_id,
                session_id=session_id,
                goal=goal,
                source_step_id=source_step_id,
                max_skills=max_skills,
                max_safety=max_safety,
            )
        except SessionGamePlayerRuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        if forwarded is not None:
            for emission in forwarded.emissions:
                _emit_compact_json(context, emission)
            if forwarded.error and not forwarded.emissions:
                raise click.ClickException(forwarded.error)
            if forwarded.exit_code != 0:
                raise click.exceptions.Exit(forwarded.exit_code)
            return

    try:
        _environment, _run, _step, source_reference = _evidence_reference_from_step(
            context,
            environment_id=environment_id,
            step_id=source_step_id,
            require_semantic_source=False,
        )
        assignment = _ingest_and_resolve_evidence_state(
            context,
            environment_id=environment_id,
            evidence_refs=[source_reference],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if assignment is None:
        raise click.ClickException("当前来源截图尚未形成可核验语义状态，未执行设备动作。")

    player = context.player()
    program = context.runtime.get("known_route_program")
    if not isinstance(program, KnownRouteProgram):
        program = KnownRouteProgram(player)
        context.runtime["known_route_program"] = program
    visual_aliases: list[str] = []
    candidate_validation_trial = False
    try:
        plan = program.plan(
            environment_id,
            assignment.state_id,
            goal,
            max_safety=max_safety,
            max_skills=max_skills,
            require_successful_run=True,
        )
    except LookupError:
        # First encounter of an equivalent visual state may be slow.  Once the
        # selected skill succeeds, replay records the alias as sourced procedural
        # memory and future routing becomes a database lookup.
        goal_sources = program.goal_source_state_ids(
            environment_id,
            goal,
            max_safety=max_safety,
            require_successful_run=True,
        )
        route_entry_sources = program.candidate_entry_state_ids(
            environment_id,
            goal,
            max_safety=max_safety,
            require_successful_run=True,
        )
        candidate_sources = list(dict.fromkeys([*goal_sources, *route_entry_sources]))
        region_fingerprint_index = _state_region_fingerprint_index(player, environment_id)
        plan = None
        for state_id in candidate_sources:
            region_distance = _state_region_fingerprint_distance(
                region_fingerprint_index,
                assignment.state_id,
                state_id,
            )
            try:
                candidate_plan = program.plan(
                    environment_id,
                    assignment.state_id,
                    goal,
                    additional_entry_state_ids=[state_id],
                    max_safety=max_safety,
                    max_skills=max_skills,
                    require_successful_run=True,
                )
            except LookupError:
                continue
            if candidate_plan.selected_entry_state_id != state_id:
                continue
            entry_skill = (
                player.get_skill_version_by_id(
                    environment_id,
                    candidate_plan.skill_version_ids[0],
                )
                if candidate_plan.skill_version_ids
                else None
            )
            if not _known_route_region_gate_allows_entry_comparison(
                region_distance,
                entry_skill,
            ):
                continue
            distance = _known_route_entry_match_distance(
                context,
                environment_id=environment_id,
                source_step_id=source_step_id,
                entry_state_id=state_id,
                skill_version_ids=candidate_plan.skill_version_ids,
                allow_control_fallback=(
                    entry_skill is not None
                    and _known_route_allows_control_only_entry_alias(entry_skill)
                ),
            )
            if distance is None:
                continue
            visual_aliases.append(state_id)
            plan = candidate_plan
            break
        if plan is None:
            # The first successful manual interaction becomes a candidate.  On
            # the next request, try one exact atomic candidate under the same
            # entry/terminal guards instead of asking the semantic Agent to
            # rediscover the control or remember a separate `skill replay`
            # command.  A failed run makes the candidate ineligible hereafter.
            trial_goal_sources = program.goal_source_state_ids(
                environment_id,
                goal,
                max_safety=max_safety,
                require_successful_run=False,
            )
            trial_route_sources = program.candidate_entry_state_ids(
                environment_id,
                goal,
                max_safety=max_safety,
                require_successful_run=False,
            )
            trial_sources = list(dict.fromkeys([*trial_goal_sources, *trial_route_sources]))
            region_fingerprint_index = _state_region_fingerprint_index(
                player,
                environment_id,
            )
            for state_id in trial_sources:
                region_distance = _state_region_fingerprint_distance(
                    region_fingerprint_index,
                    assignment.state_id,
                    state_id,
                )
                try:
                    candidate_plan = program.plan(
                        environment_id,
                        assignment.state_id,
                        goal,
                        additional_entry_state_ids=[state_id],
                        max_safety=max_safety,
                        max_skills=1,
                        require_successful_run=False,
                    )
                except LookupError:
                    continue
                if candidate_plan.selected_entry_state_id != state_id:
                    continue
                uses_entry_alias = state_id != assignment.state_id
                if not _known_route_allows_virgin_candidate_trial(
                    player,
                    environment_id=environment_id,
                    plan=candidate_plan,
                    uses_entry_alias=uses_entry_alias,
                ):
                    continue
                entry_skill = player.get_skill_version_by_id(
                    environment_id,
                    candidate_plan.skill_version_ids[0],
                )
                if not _known_route_region_gate_allows_entry_comparison(
                    region_distance,
                    entry_skill,
                ):
                    continue
                distance = _known_route_entry_match_distance(
                    context,
                    environment_id=environment_id,
                    source_step_id=source_step_id,
                    entry_state_id=state_id,
                    skill_version_ids=candidate_plan.skill_version_ids,
                    allow_control_fallback=(
                        entry_skill is not None
                        and _known_route_allows_control_only_entry_alias(entry_skill)
                    ),
                )
                if distance is None:
                    continue
                if uses_entry_alias:
                    visual_aliases.append(state_id)
                plan = candidate_plan
                candidate_validation_trial = True
                break
        if plan is None:
            raise click.ClickException(
                f"没有从当前界面通往“{goal}”的已学习路径；设备未执行动作，交回语义探索。"
            )

    if not plan.skill_version_ids:
        payload = {
            "schema": "game-observatory.ai-player.known-route-execution.v1",
            "route": plan,
            "executions": [],
            "terminal_evidence_step_id": source_step_id,
            "model_input_tokens": 0,
        }
        if context.agent_brief:
            _emit_compact_json(
                context,
                {
                    "schema": "game-observatory.ai-player.known-route-brief.v1",
                    "ok": True,
                    "goal": goal,
                    "already_at_goal": True,
                    "skills": 0,
                    "actions": 0,
                    "model_input_tokens": 0,
                    "terminal_evidence_step_id": source_step_id,
                },
            )
        else:
            _emit(context, payload, summary=f"当前已经位于目标：{goal}")
        return

    arcs = program.arcs(
        environment_id,
        max_safety=max_safety,
        require_successful_run=not candidate_validation_trial,
    )
    current_source_step_id = source_step_id
    executions: list[dict[str, Any]] = []
    original_state_id = assignment.state_id
    shared_live_route_runtime = context.runtime.get("session_live_route_runtime")
    owns_live_route_runtime = not isinstance(
        shared_live_route_runtime,
        LiveStepRouteRuntime,
    )
    if owns_live_route_runtime:
        shared_live_route_runtime = LiveStepRouteRuntime.create(
            facility=context.facility(),
            player=player,
        )
        if context.runtime.get("session_runtime_host"):
            context.runtime["session_live_route_runtime"] = shared_live_route_runtime
            owns_live_route_runtime = False
    else:
        context.runtime["session_live_route_runtime"] = shared_live_route_runtime
    verified_entry_aliases = _known_route_verified_entry_aliases(
        program,
        environment_id=environment_id,
        observed_state_id=original_state_id,
        first_skill_version_id=plan.skill_version_ids[0],
        visually_verified_entry_state_ids=visual_aliases,
    )
    for index, skill_version_id in enumerate(plan.skill_version_ids):
        verified_entry_aliases.update(
            (candidate_skill_id, required_state_id)
            for candidate_skill_id, required_state_id in program.remembered_skill_entry_aliases(
                environment_id,
                plan.state_ids[index],
            )
            if candidate_skill_id == skill_version_id
        )
    nested_runtime: dict[str, Any] = {
        "facility": context.facility(),
        "player": player,
        "live_route_runtime": shared_live_route_runtime,
        "known_source_assignment": {
            "source_step_id": source_step_id,
            "assignment": assignment,
        },
        "known_source_reference": {
            "source_step_id": source_step_id,
            "reference": source_reference,
        },
        "known_route_pending_evidence_refs": [],
        "defer_skill_lifecycle_reconcile": True,
        "verified_skill_entry_aliases": verified_entry_aliases,
        "suppress_output": True,
    }
    for index, skill_version_id in enumerate(plan.skill_version_ids):
        from_state_id = plan.state_ids[index]
        arc = next(
            (
                item
                for item in arcs
                if item.skill_version_id == skill_version_id and item.from_state_id == from_state_id
            ),
            None,
        )
        skill = player.get_skill_version_by_id(environment_id, skill_version_id)
        if arc is None or skill is None:
            if owns_live_route_runtime:
                shared_live_route_runtime.close()
            raise click.ClickException(
                f"已学习路径在执行前发生漂移：{skill_version_id}；设备未继续动作。"
            )
        declared_sources = set(skill.applicability_scope.required_state_ids)
        if from_state_id not in declared_sources:
            source_alias_id = _known_route_required_state_alias(
                program,
                environment_id=environment_id,
                observed_state_id=from_state_id,
                skill=skill,
                max_safety=max_safety,
            )
            if source_alias_id is None:
                if owns_live_route_runtime:
                    shared_live_route_runtime.close()
                raise click.ClickException(
                    f"已学习路径缺少唯一的入口别名：{skill_version_id}；设备未继续动作。"
                )
        else:
            source_alias_id = (
                from_state_id if index == 0 and original_state_id != from_state_id else None
            )
        allow_candidate = skill.status != "preferred" or source_alias_id is not None
        sink: list[dict[str, Any]] = []
        nested_runtime["emission_sink"] = sink
        nested_runtime["defer_known_route_terminal_ingest"] = True
        if index == len(plan.skill_version_ids) - 1:
            nested_runtime[_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY] = {
                "skill_version_id": skill.id,
                "goal_alias": goal,
                "provenance": "known_route",
            }
        else:
            nested_runtime.pop(_KNOWN_SKILL_GOAL_ALIAS_REQUEST_KEY, None)
        nested_context = _PlayerCLIContext(
            root=context.root,
            as_json=True,
            agent_brief=False,
            runtime=nested_runtime,
        )
        replay_callback = getattr(player_skill_replay.callback, "__wrapped__", None)
        if not callable(replay_callback):
            if owns_live_route_runtime:
                shared_live_route_runtime.close()
            raise click.ClickException("固定路径执行入口不可用，设备未继续动作。")
        try:
            replay_callback(
                nested_context,
                skill_id=skill_version_id,
                environment_id=environment_id,
                session_id=session_id,
                source_step_id=current_source_step_id,
                source_state_alias_id=source_alias_id,
                allow_candidate=allow_candidate,
                visual_variant_id=(f"known-route.{original_state_id}" if allow_candidate else None),
                independent_reset_id=(
                    f"known-route.continuity.{environment_id}" if allow_candidate else None
                ),
                baseline_model_input_tokens=(
                    arc.median_baseline_model_input_tokens if allow_candidate else 0
                ),
                baseline_decision_latency_ms=(
                    arc.median_baseline_decision_latency_ms if allow_candidate else 0.0
                ),
                max_safety=max_safety,
            )
        except click.exceptions.Exit as exc:
            if owns_live_route_runtime:
                shared_live_route_runtime.close()
            failed_execution = sink[-1] if sink else None
            failed_run = failed_execution.get("run") if isinstance(failed_execution, dict) else None
            failed_steps = (
                failed_execution.get("steps", []) if isinstance(failed_execution, dict) else []
            )
            failed_step = failed_steps[-1] if failed_steps else None
            actual_state_id = next(
                (
                    result.observed_state_id
                    for result in reversed(failed_steps)
                    if getattr(result, "observed_state_id", None) is not None
                ),
                None,
            )
            terminal_evidence_step_id = (
                str(failed_execution.get("terminal_evidence_step_id"))
                if isinstance(failed_execution, dict)
                and failed_execution.get("terminal_evidence_step_id")
                else current_source_step_id
            )
            terminal_step = context.facility().store.get_evidence_step(terminal_evidence_step_id)
            terminal_support = _action_agent_support(
                context,
                {
                    "evidence_step_id": terminal_evidence_step_id,
                    "after_artifact_id": (
                        terminal_step.after_frame_id if terminal_step is not None else None
                    ),
                },
            )
            failed_run_id = getattr(failed_run, "id", None)
            completed_route_actions = sum(
                int(getattr(execution.get("run"), "action_count", 0))
                for execution in executions
                if isinstance(execution, dict)
            )
            failed_skill_actions = int(getattr(failed_run, "action_count", 0))
            can_confirm_terminal_alias = bool(
                actual_state_id and _is_caller_dependent_skill(skill) and failed_run_id
            )
            failure_payload = {
                "schema": "game-observatory.ai-player.known-route-interruption.v1",
                "ok": False,
                "goal": goal,
                "failed_skill_version_id": skill.id,
                "failed_skill_title": skill.title,
                "source_state_id": from_state_id,
                "expected_terminal_state_id": _skill_terminal_state_id(skill),
                "actual_terminal_state_id": actual_state_id,
                "terminal_evidence_step_id": terminal_evidence_step_id,
                "after_preview": terminal_support["after_preview"],
                "run_id": failed_run_id,
                "actions": completed_route_actions + failed_skill_actions,
                "completed_skill_count": len(executions),
                "failed_skill_actions": failed_skill_actions,
                "model_input_tokens": getattr(failed_run, "model_input_tokens", 0),
                "failed_step_id": getattr(failed_step, "step_id", None),
                "failed_step_outcome": getattr(failed_step, "outcome", None),
                "failed_step_summary": getattr(failed_step, "result_summary", None),
                "can_confirm_terminal_alias": can_confirm_terminal_alias,
                "confirm_terminal_alias_command": (
                    "omni game player --json --agent-brief skill "
                    f"confirm-terminal-alias {skill.id} "
                    f"--environment {environment_id} "
                    f"--failed-run {failed_run_id} "
                    f"--source-step {terminal_evidence_step_id} "
                    f"--source-state {from_state_id} "
                    '--meaning "<根据同一回执的 after_preview 描述实际终态为何满足目标>"'
                    if can_confirm_terminal_alias
                    else None
                ),
            }
            if context.agent_brief:
                receipt = _atomic_agent_receipt(
                    context,
                    category="known-routes",
                    payload=failure_payload,
                )
                failure_payload.update(
                    {
                        "receipt_ref": receipt["path"],
                        "receipt_sha256": receipt["sha256"],
                    }
                )
                _emit_compact_json(context, failure_payload)
            else:
                _emit(
                    context,
                    failure_payload,
                    summary=f"已学习路径在 {skill.title} 安全中断。",
                )
            raise click.exceptions.Exit(1) from exc
        except Exception:
            if owns_live_route_runtime:
                shared_live_route_runtime.close()
            raise
        if not sink:
            if owns_live_route_runtime:
                shared_live_route_runtime.close()
            raise click.ClickException("固定路径执行缺少结构化回执；未继续后续路径。")
        execution = sink[-1]
        run = execution.get("run")
        if getattr(run, "outcome", None) != "success":
            if owns_live_route_runtime:
                shared_live_route_runtime.close()
            raise click.ClickException(
                f"已学习路径在 {skill.title} 未通过终态守卫；未继续后续路径。"
            )
        current_source_step_id = str(execution["terminal_evidence_step_id"])
        executions.append(execution)
    if owns_live_route_runtime:
        shared_live_route_runtime.close()

    skill_run_ids = [execution["run"].id for execution in executions]
    if context.runtime.get("session_runtime_host"):
        enqueue_sedimentation = context.runtime.get("deferred_sedimentation_enqueue")
        if not callable(enqueue_sedimentation):
            raise click.ClickException(
                "fixed-route evidence is retained but the session sedimentation "
                "queue is unavailable"
            )
        try:
            sedimentation = enqueue_sedimentation(skill_run_ids)
        except (OSError, TypeError, ValueError, SessionGamePlayerRuntimeError) as exc:
            raise click.ClickException(
                "fixed-route evidence is retained but background sedimentation "
                f"was not accepted: {type(exc).__name__}: {exc}"
            ) from exc
    else:
        try:
            sedimentation = settle_deferred_skill_runs(
                context.facility().store.root,
                environment_id=environment_id,
                skill_run_ids=skill_run_ids,
            )
        except (KeyError, OSError, TypeError, ValueError, SkillLifecycleError) as exc:
            # Physical evidence and signed SkillRuns are already canonical.  Do not
            # report a reusable route until lifecycle reconciliation, graph ingest,
            # and reopen verification have all crossed the route boundary.
            raise click.ClickException(
                "fixed-route evidence is retained but deferred sedimentation failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    model_input_tokens = sum(int(execution["run"].model_input_tokens) for execution in executions)
    terminal_step = context.facility().store.get_evidence_step(current_source_step_id)
    terminal_support = _action_agent_support(
        context,
        {
            "evidence_step_id": current_source_step_id,
            "after_artifact_id": (
                terminal_step.after_frame_id if terminal_step is not None else None
            ),
        },
    )
    full_payload = {
        "schema": "game-observatory.ai-player.known-route-execution.v1",
        "route": plan,
        "executions": executions,
        "terminal_evidence_step_id": current_source_step_id,
        "model_input_tokens": model_input_tokens,
        "sedimentation": sedimentation,
    }
    if context.agent_brief:
        receipt = _atomic_agent_receipt(
            context,
            category="known-routes",
            payload={"ok": True, **full_payload},
        )
        current_session = AIPlayerSessionControl(player).get_session(
            environment_id,
            session_id,
        )
        _emit_compact_json(
            context,
            {
                "schema": "game-observatory.ai-player.known-route-brief.v1",
                "ok": True,
                "goal": goal,
                "goal_state_id": plan.goal_state_id,
                "route_mode": (
                    "candidate_validation" if candidate_validation_trial else "learned_route"
                ),
                "skills": len(executions),
                "executed_skills": [
                    {
                        "version_id": execution["skill"].id,
                        "title": execution["skill"].title,
                        "terminal_state_id": _skill_terminal_state_id(execution["skill"]),
                    }
                    for execution in executions
                ],
                "actions": sum(execution["run"].action_count for execution in executions),
                "model_input_tokens": model_input_tokens,
                "terminal_evidence_step_id": current_source_step_id,
                "after_preview": terminal_support["after_preview"],
                "remaining_actions": (
                    current_session.remaining_action_budget if current_session is not None else None
                ),
                "receipt_ref": receipt["path"],
                "receipt_sha256": receipt["sha256"],
            },
        )
    else:
        _emit(
            context,
            full_payload,
            summary=(
                f"已通过 {len(executions)} 条固定技能到达目标“{goal}”，"
                f"模型输入 token 为 {model_input_tokens}。"
            ),
        )


@player_skill.command("crystallize")
@click.option(
    "--file",
    "request_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.pass_obj
def player_skill_crystallize(
    context: _PlayerCLIContext,
    request_path: Path,
) -> None:
    """从连续、成功、已闭合的状态转移生成 immutable candidate 技能。"""

    try:
        request = SkillCrystallizationRequestV1.model_validate(_read_json_object(request_path))
        skill = SkillCrystallizer(context.player()).crystallize(request)
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.skill-crystallize-result.v1",
            "skill": skill,
        },
        summary=f"已结晶候选技能：{skill.id}",
    )


@player_skill.command("validate")
@click.argument("skill_version_id")
@click.option("--environment", "environment_id", required=True)
@click.option("--evaluator", required=True)
@click.option("--run", "run_ids", multiple=True, required=True)
@click.pass_obj
def player_skill_validate(
    context: _PlayerCLIContext,
    skill_version_id: str,
    environment_id: str,
    evaluator: str,
    run_ids: tuple[str, ...],
) -> None:
    """从独立、已签名的 immutable SkillRun 推导验证门并追加结果。"""

    player = context.player()
    runs = [player.get_skill_run(environment_id, item) for item in run_ids]
    missing = [item for item, run in zip(run_ids, runs, strict=True) if run is None]
    if missing:
        raise click.ClickException(f"SkillRun 不存在: {', '.join(missing)}")
    try:
        validation = derive_skill_validation(
            environment_id=environment_id,
            skill_version_id=skill_version_id,
            evaluator=evaluator,
            runs=[item for item in runs if item is not None],
        )
        stored = player.append_skill_validation(validation)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.skill-validation-result.v1",
            "validation": stored,
        },
        summary=f"技能验证 {stored.id}：{stored.status}",
    )


@cmd_player.group("guide")
def player_guide() -> None:
    """带时效、版本、作者、平台和来源的攻略知识。"""


@player_guide.command("search")
@click.argument("query")
@click.option("--environment", "environment_id", required=True)
@click.option(
    "--status",
    type=click.Choice(["current", "unverified", "stale", "contradicted"]),
    default=None,
)
@click.option("--limit", type=click.IntRange(1, 200), default=20, show_default=True)
@click.pass_obj
def player_guide_search(
    context: _PlayerCLIContext,
    query: str,
    environment_id: str,
    status: str | None,
    limit: int,
) -> None:
    """检索已导入的真实攻略来源；结果保留 URL、作者、时间和适用域。"""

    needle = query.casefold().strip()
    if not needle:
        raise click.UsageError("query 不能为空")
    guides = context.player().list_guide_knowledge(environment_id)
    if status is not None:
        guides = [item for item in guides if item.status == status]
    matches = []
    for item in guides:
        haystack = "\n".join(
            [
                item.id,
                str(item.url),
                item.platform,
                item.author,
                item.summary,
                *item.locators,
            ]
        ).casefold()
        if needle in haystack:
            matches.append(item)
    matches = matches[-limit:]
    _emit(
        context,
        {"schema": "game-observatory.ai-player.guide-search.v1", "guides": matches},
        summary=f"攻略命中：{len(matches)} 条",
    )


@player_guide.command("import")
@click.option("--environment", "environment_id", required=True)
@click.option(
    "--file",
    "input_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--research-record-id",
    default=None,
    help="导入 sanguo-guide-seed.v1 时必须提供并与已保存 SourceSnapshot 一致。",
)
@click.pass_obj
def player_guide_import(
    context: _PlayerCLIContext,
    environment_id: str,
    input_path: Path,
    research_record_id: str | None,
) -> None:
    """导入带真实平台来源快照的攻略知识，不接受无来源结论。"""

    payload = _read_json_object(input_path)
    player = context.player()
    _require_environment(player, environment_id)
    try:
        if payload.get("schema") == "game-observatory.ai-player.guide-import-bundle.v1":
            snapshots = [
                SourceSnapshot.model_validate(item) for item in payload.get("source_snapshots", [])
            ]
            guides = [GuideKnowledgeV1.model_validate(item) for item in payload.get("guides", [])]
            if not snapshots or not guides:
                raise ValueError("guide import bundle requires source_snapshots and guides")
            result = player.apply_knowledge_memory_seed(
                environment_id,
                snapshots,
                guides,
                [],
            )
            imported = guides
        elif payload.get("schema") == "sanguo-guide-seed.v1":
            if not research_record_id:
                raise click.UsageError("sanguo-guide-seed.v1 requires --research-record-id")
            imported = load_guide_seed(
                input_path,
                environment_id=environment_id,
                research_record_id=research_record_id,
            )
            for guide in imported:
                player.append_guide_knowledge(guide)
            result = {"inserted_guide_count": len(imported)}
        else:
            guide = GuideKnowledgeV1.model_validate(payload)
            if guide.environment_id != environment_id:
                raise ValueError("guide environment does not match --environment")
            imported = [player.append_guide_knowledge(guide)]
            result = {"inserted_guide_count": 1}
    except click.ClickException:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.guide-import-result.v1",
            "result": result,
            "guides": imported,
        },
        summary=f"已导入攻略：{len(imported)} 条",
    )


@player_guide.command("freshness")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_guide_freshness(context: _PlayerCLIContext, environment_id: str) -> None:
    """列出攻略适用性和缺失理由；未验证结论只用于发现。"""

    guides = context.player().list_guide_knowledge(environment_id)
    items = [
        {
            "guide": item,
            "discovery_only": item.status == "unverified"
            or bool(item.missing_applicability_reason),
        }
        for item in guides
    ]
    _emit(
        context,
        {"schema": "game-observatory.ai-player.guide-freshness-list.v1", "items": items},
        summary=f"攻略知识：{len(items)} 条",
    )


player_guide.add_command(guide_refresh_group)


def _sanguo_daily_coordinator(context: _PlayerCLIContext) -> SanguoDailyCoordinator:
    return SanguoDailyCoordinator(SanguoDailyContinuityLedger(context.player()))


def _daily_candidate_from_file(
    path: Path,
    model: type[SanguoDailyDutyCandidateV1] | type[SanguoDailySealCandidateV1],
) -> SanguoDailyDutyCandidateV1 | SanguoDailySealCandidateV1:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"无法读取连续日候选 {path}: {exc}") from exc


@cmd_player.group("daily")
def player_daily() -> None:
    """三谋自然日职责：只读状态、单步推进与严格封账。"""


@player_daily.command("schema")
@click.pass_obj
def player_daily_schema(context: _PlayerCLIContext) -> None:
    """输出职责候选与封账候选的完整 JSON Schema。"""

    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.sanguo-daily-candidate-schemas.v1",
            "duty_order": list(DAILY_DUTIES),
            "duty_candidate_schema": SanguoDailyDutyCandidateV1.model_json_schema(by_alias=True),
            "seal_candidate_schema": SanguoDailySealCandidateV1.model_json_schema(by_alias=True),
            "rules": [
                "候选不接受日期、日序或账本版本；协调层只使用可信系统当天。",
                "每次 advance 最多提交固定次序中的下一项。",
                "职责语义必须由提交者明确声明，不能从任意点击自动推断。",
                "所有链接交由连续日账本按 canonical 数据库真值校验。",
                "seal 不接受动作质量输入，只读取设施已经保存的真实样本。",
            ],
        },
        summary="已输出三谋连续日候选契约",
    )


@player_daily.command("status")
@click.option("--environment", "environment_id", required=True)
@click.option("--run", "continuity_run_id", required=True)
@click.pass_obj
def player_daily_status(
    context: _PlayerCLIContext,
    environment_id: str,
    continuity_run_id: str,
) -> None:
    """只读查看当前自然日、下一职责与封账条件。"""

    try:
        status = _sanguo_daily_coordinator(context).status(
            environment_id,
            continuity_run_id,
        )
    except SanguoDailyContinuityError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.sanguo-daily-status-result.v1",
            "status": status,
        },
        summary=(
            f"连续日 Day {status.schedule.day_index}: "
            f"{status.schedule.status}; 下一职责 {status.schedule.next_duty or '无'}"
        ),
    )


@player_daily.command("advance")
@click.option(
    "--candidate",
    "candidate_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.pass_obj
def player_daily_advance(
    context: _PlayerCLIContext,
    candidate_path: Path,
) -> None:
    """用真实 canonical 链接提交候选；每次最多写入下一项职责。"""

    candidate = _daily_candidate_from_file(candidate_path, SanguoDailyDutyCandidateV1)
    assert isinstance(candidate, SanguoDailyDutyCandidateV1)
    try:
        day = _sanguo_daily_coordinator(context).advance(candidate)
    except SanguoDailyContinuityError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.sanguo-daily-advance-result.v1",
            "candidate_id": candidate.candidate_id,
            "day": day,
        },
        summary=(f"已记录 Day {day.day_index} 的 {day.duties[-1].duty}; 当前 {len(day.duties)}/6"),
    )


@player_daily.command("seal")
@click.option(
    "--candidate",
    "candidate_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.pass_obj
def player_daily_seal(
    context: _PlayerCLIContext,
    candidate_path: Path,
) -> None:
    """六项职责和真实动作质量样本齐备后封账。"""

    candidate = _daily_candidate_from_file(candidate_path, SanguoDailySealCandidateV1)
    assert isinstance(candidate, SanguoDailySealCandidateV1)
    try:
        day = _sanguo_daily_coordinator(context).seal(candidate)
    except SanguoDailyContinuityError as exc:
        raise click.ClickException(f"{exc.code}: {exc}") from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.sanguo-daily-seal-result.v1",
            "candidate_id": candidate.candidate_id,
            "day": day,
        },
        summary=f"已封账 Day {day.day_index}: {day.natural_day.isoformat()}",
    )


@cmd_player.group("evidence")
def player_evidence() -> None:
    """Before、Action、After、点击标记、视频帧和来源。"""


@player_evidence.command("run")
@click.argument("run_id")
@click.pass_obj
def player_evidence_run(context: _PlayerCLIContext, run_id: str) -> None:
    """查看 EvidenceRun 及全部步骤。"""

    store = context.facility().store
    run = store.get_evidence_run(run_id)
    if run is None:
        raise click.ClickException(f"没有这个 EvidenceRun: {run_id}")
    steps = store.list_evidence_steps(run_id)
    _emit(
        context,
        {"schema": "game-observatory.evidence-run-detail.v1", "run": run, "steps": steps},
        summary=f"EvidenceRun {run.id}：{len(steps)} 步",
    )


@player_evidence.command("step")
@click.argument("step_id")
@click.pass_obj
def player_evidence_step(context: _PlayerCLIContext, step_id: str) -> None:
    """查看一步动作、前后图、点击区域和终态判断。"""

    store = context.facility().store
    step = store.get_evidence_step(step_id)
    if step is None:
        raise click.ClickException(f"没有这个 EvidenceStep: {step_id}")
    artifacts = [
        store.get_artifact(artifact_id)
        for artifact_id in step.artifact_ids
        if store.get_artifact(artifact_id) is not None
    ]
    _emit(
        context,
        {
            "schema": "game-observatory.evidence-step-detail.v1",
            "step": step,
            "artifacts": artifacts,
        },
        summary=f"EvidenceStep {step.id}: {step.status}",
    )


@player_evidence.command("show")
@click.argument("artifact_id")
@click.pass_obj
def player_evidence_show(context: _PlayerCLIContext, artifact_id: str) -> None:
    """按稳定 artifact ID 查看文件位置、hash 和媒体信息。"""

    artifact = context.facility().store.get_artifact(artifact_id)
    if artifact is None:
        raise click.ClickException(f"没有这个 artifact: {artifact_id}")
    _emit(
        context,
        {"schema": "game-observatory.artifact-detail.v1", "artifact": artifact},
        summary=f"Artifact {artifact.id}: {artifact.kind}",
    )


@player_evidence.command("export")
@click.argument("run_id")
@click.option(
    "--output",
    "output_dir",
    type=click.Path(path_type=Path, file_okay=False),
    required=True,
)
@click.option("--include-media", is_flag=True, help="按内容 hash 去重复制媒体；默认只导出清单。")
@click.pass_obj
def player_evidence_export(
    context: _PlayerCLIContext,
    run_id: str,
    output_dir: Path,
    include_media: bool,
) -> None:
    """导出一个动作运行的结构化清单；媒体按 hash 只保存一份。"""

    store = context.facility().store
    run = store.get_evidence_run(run_id)
    manifest = store.get_evidence_manifest(run_id)
    if run is None:
        raise click.ClickException(f"没有这个 EvidenceRun: {run_id}")
    steps = store.list_evidence_steps(run_id)
    artifact_ids = list(dict.fromkeys(item for step in steps for item in step.artifact_ids))
    artifacts = []
    for artifact_id in artifact_ids:
        artifact = store.get_artifact(artifact_id)
        if artifact is None or not Path(artifact.path).is_file():
            raise click.ClickException(f"证据 artifact 缺失: {artifact_id}")
        artifacts.append(artifact)
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    media_by_hash: dict[str, str] = {}
    if include_media:
        media_dir = destination / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        for artifact in artifacts:
            if artifact.sha256 in media_by_hash:
                continue
            source = Path(artifact.path)
            suffix = source.suffix.lower() or ".bin"
            target = media_dir / f"{artifact.sha256}{suffix}"
            if not target.is_file():
                shutil.copy2(source, target)
            media_by_hash[artifact.sha256] = str(target.relative_to(destination))
    payload = {
        "schema": "game-observatory.ai-player.evidence-export.v1",
        "run": run.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json") if manifest is not None else None,
        "steps": [item.model_dump(mode="json") for item in steps],
        "artifacts": [
            {
                **item.model_dump(mode="json"),
                "exported_media": media_by_hash.get(item.sha256),
            }
            for item in artifacts
        ],
        "unique_media_count": len({item.sha256 for item in artifacts}),
        "canonical_artifact_count": len(artifacts),
    }
    output_path = destination / "evidence.json"
    temporary = output_path.with_suffix(f".json.tmp-{uuid.uuid4().hex}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(output_path)
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.evidence-export-result.v1",
            "path": str(output_path),
            "artifact_count": len(artifacts),
            "unique_media_count": payload["unique_media_count"],
            "media_included": include_media,
        },
        summary=f"证据已导出：{output_path}",
    )


@player_evidence.command("prune")
@click.option("--older-than-hours", type=click.FloatRange(min=0), default=24, show_default=True)
@click.option("--apply", is_flag=True, help="仅删除未登记且超过时限的孤儿文件。")
@click.pass_obj
def player_evidence_prune(
    context: _PlayerCLIContext,
    older_than_hours: float,
    apply: bool,
) -> None:
    """扫描静帧重复和孤儿文件；canonical 证据保持不可变。"""

    store = context.facility().store
    artifacts = store.list_artifacts()
    registered_paths = {Path(item.path).resolve() for item in artifacts}
    artifact_root = store.artifact_root.resolve()
    threshold = time.time() - older_than_hours * 3600
    orphan_files = [
        path.resolve()
        for path in artifact_root.rglob("*")
        if path.is_file()
        and path.resolve() not in registered_paths
        and path.stat().st_mtime <= threshold
    ]
    by_hash: dict[str, list[str]] = {}
    for artifact in artifacts:
        by_hash.setdefault(artifact.sha256, []).append(artifact.id)
    duplicate_groups = [
        {"sha256": digest, "artifact_ids": ids}
        for digest, ids in sorted(by_hash.items())
        if len(ids) > 1
    ]
    deleted: list[str] = []
    if apply:
        for path in orphan_files:
            if not path.is_relative_to(artifact_root):
                raise click.ClickException(f"拒绝删除 artifact root 外的文件: {path}")
            path.unlink()
            deleted.append(str(path))
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.evidence-prune-result.v1",
            "mode": "apply" if apply else "dry-run",
            "orphan_files": [str(item) for item in orphan_files],
            "deleted_files": deleted,
            "duplicate_registered_groups": duplicate_groups,
            "canonical_policy": "已登记证据不可变；导出时按 hash 去重，不删除来源记录。",
        },
        summary=(
            f"证据清理：{len(orphan_files)} 个孤儿候选，"
            f"{len(duplicate_groups)} 组 canonical 重复 hash"
        ),
    )


@cmd_player.group("benchmark")
def player_benchmark() -> None:
    """已知真值、持续 Session 和外部模型比较基准。"""


@player_benchmark.command("list")
@click.pass_obj
def player_benchmark_list(context: _PlayerCLIContext) -> None:
    """列出 AFKJ B0—B5、候选模型、质量硬门和时延门。"""

    manifest = build_afkj_external_agent_manifest()
    _emit(
        context,
        {"schema": "game-observatory.ai-player.benchmark-list.v1", "benchmarks": [manifest]},
        summary="外部 Agent 基准：1 套（AFKJ B0—B5）",
    )


@player_benchmark.command("run")
@click.option(
    "--candidate",
    "candidate_id",
    type=click.Choice(
        [
            "claude-sonnet-5-medium",
            "gpt-5.6-terra-medium",
            "gpt-5.6-luna-medium",
        ]
    ),
    required=True,
)
@click.option("--task", "task_id", type=click.Choice(["B0", "B1", "B3"]), default="B0")
@click.option("--repetition", type=click.IntRange(min=1), default=1)
@click.option("--timeout", "timeout_seconds", type=click.FloatRange(min=10), default=120.0)
@click.option(
    "--limit",
    "case_limit",
    type=click.IntRange(min=1, max=20),
    help="B1/B3 诊断时限制图像案例数；B1 最多 20 例，B3 最多 10 例。",
)
@click.option(
    "--case",
    "case_ids",
    multiple=True,
    type=click.Choice(
        [f"B1-{index:02d}" for index in range(1, 21)]
        + [f"B3-{index:02d}" for index in range(1, 11)]
    ),
    help="B1/B3 的明确诊断案例，可重复传入；不得和 --limit 同用。",
)
@click.option("--output-root", type=click.Path(path_type=Path, file_okay=False))
@click.option("--run-id", help="可选稳定运行 ID；默认生成唯一 ID。")
@click.option(
    "--resume-existing",
    is_flag=True,
    help="续跑同一 B1/B3 run；必须同时提供原 --run-id 和完全相同的参数。",
)
@click.pass_obj
def player_benchmark_run(
    context: _PlayerCLIContext,
    candidate_id: str,
    task_id: str,
    repetition: int,
    timeout_seconds: float,
    case_limit: int | None,
    case_ids: tuple[str, ...],
    output_root: Path | None,
    run_id: str | None,
    resume_existing: bool,
) -> None:
    """运行可审计的 AFKJ 外部 Agent 基准。"""

    repository_root = _repository_root()
    check_external_agent_contracts(repository_root)
    runner = AFKJExternalAgentBenchmarkRunner(repository_root)
    if task_id == "B0":
        if resume_existing:
            raise click.UsageError("--resume-existing 只适用于 B1/B3")
        if case_limit is not None or case_ids:
            raise click.UsageError("--limit 和 --case 只适用于 B1/B3")
        result, result_path = asyncio.run(
            runner.run_b0(
                candidate_id=candidate_id,
                repetition=repetition,
                timeout_seconds=timeout_seconds,
                output_root=output_root,
                run_id=run_id,
            )
        )
        summary = (
            f"AFKJ B0 {candidate_id}: "
            f"{'PASS' if result.quality_pass else 'FAIL'} "
            f"({result.b0_correct}/10, warm {result.warm_probe_correct}/4)"
        )
    elif task_id == "B1":
        if resume_existing and not run_id:
            raise click.UsageError("--resume-existing 必须同时提供 --run-id")
        if case_limit is not None and case_ids:
            raise click.UsageError("--limit 和 --case 不能同时使用")
        if any(not item.startswith("B1-") for item in case_ids):
            raise click.UsageError("B1 只能使用 B1-* 案例")
        result, result_path = asyncio.run(
            runner.run_b1(
                candidate_id=candidate_id,
                repetition=repetition,
                timeout_seconds=timeout_seconds,
                case_limit=case_limit or 20,
                case_ids=case_ids,
                output_root=output_root,
                run_id=run_id,
                resume_existing=resume_existing,
            )
        )
        mode = (
            "正式"
            if result.formal_quality_eligible
            else "完整候选"
            if result.complete_case_set
            else "冒烟"
        )
        summary = (
            f"AFKJ B1 {mode} {candidate_id}: "
            f"有效定位 {result.target_hits}/{result.requested_case_count}，"
            f"对象 {result.object_correct}/{result.requested_case_count}，"
            f"状态 {result.state_correct}/{result.requested_case_count}，"
            f"自由坐标 {result.raw_target_hits}/{result.raw_target_estimate_count}"
        )
    else:
        if resume_existing and not run_id:
            raise click.UsageError("--resume-existing 必须同时提供 --run-id")
        if case_limit is not None and case_ids:
            raise click.UsageError("--limit 和 --case 不能同时使用")
        if case_limit is not None and case_limit > 10:
            raise click.UsageError("B3 --limit 不能超过 10")
        if any(not item.startswith("B3-") for item in case_ids):
            raise click.UsageError("B3 只能使用 B3-* 案例")
        result, result_path = asyncio.run(
            runner.run_b3(
                candidate_id=candidate_id,
                repetition=repetition,
                timeout_seconds=timeout_seconds,
                case_limit=case_limit or 10,
                case_ids=case_ids,
                output_root=output_root,
                run_id=run_id,
                resume_existing=resume_existing,
            )
        )
        mode = "完整候选图像诊断" if result.full_image_diagnostic_set else "候选冒烟"
        summary = (
            f"AFKJ B3 {mode} {candidate_id}: "
            f"完成 {result.completed_case_count}/{result.requested_case_count}，"
            f"召回 {result.expected_recall:.1%}，精确 {result.precision:.1%}，"
            f"正式选型资格=无"
        )
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.benchmark-run-result.v1",
            "result_path": str(result_path),
            "result": result,
        },
        summary=summary,
    )


@player_benchmark.command("compare")
@click.argument(
    "result_paths",
    nargs=-1,
    required=True,
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
)
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False))
@click.pass_obj
def player_benchmark_compare(
    context: _PlayerCLIContext,
    result_paths: tuple[Path, ...],
    output: Path | None,
) -> None:
    """按同口径汇总一个或多个 AFKJ B0、B1 或 B3 result.json。"""

    schemas = {json.loads(path.read_text(encoding="utf-8")).get("schema") for path in result_paths}
    if schemas == {"game-observatory.ai-player.afkj-external-agent-b0-result.v1"}:
        comparison = compare_b0_results([load_b0_result(path) for path in result_paths])
    elif schemas and schemas <= {
        "game-observatory.ai-player.afkj-external-agent-b1-result.v1",
        "game-observatory.ai-player.afkj-external-agent-b1-result.v2",
    }:
        comparison = compare_b1_results([load_b1_result(path) for path in result_paths])
    elif schemas == {"game-observatory.ai-player.afkj-external-agent-b3-result.v1"}:
        comparison = compare_b3_results([load_b3_result(path) for path in result_paths])
    else:
        raise click.UsageError("比较输入必须全部属于同一个 AFKJ B0、B1 或 B3 schema")
    comparison["source_result_paths"] = [str(path.resolve()) for path in result_paths]
    comparison["generated_at"] = utc_now()
    if output is not None:
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(f"{output.suffix}.tmp-{uuid.uuid4().hex}")
        temporary.write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(output)
        comparison["output_path"] = str(output)
    _emit(
        context,
        comparison,
        summary=(
            f"AFKJ {comparison['task_id']} 比较：{len(comparison['rows'])} 个候选，"
            f"{len(result_paths)} 个样本"
        ),
    )


@cmd_player.group("account")
def player_account() -> None:
    """账号身份、指标、行为和发言政策。"""


@player_account.command("initialize")
@click.option("--environment", "environment_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option(
    "--ai-label",
    default="AI 玩家",
    show_default=True,
    help="对外需要说明身份时使用的固定口径；不得冒充真人。",
)
@click.pass_obj
def player_account_initialize(
    context: _PlayerCLIContext,
    environment_id: str,
    source_step_id: str,
    ai_label: str,
) -> None:
    """初始化固定权限边界：游戏内自主，支付与外部身份资料逐次授权。"""

    player = context.player()
    _environment, _run, _step, reference = _evidence_reference_from_step(
        context,
        environment_id=environment_id,
        step_id=source_step_id,
    )
    existing = player.get_account_policy(environment_id)
    if existing is not None:
        if existing.ai_identity_label != ai_label:
            raise click.ClickException(
                "账号策略已经存在且 AI 身份口径不同；请追加显式新版本，不能静默覆盖。"
            )
        stored = existing
        inserted = 0
    else:
        stored = player.append_account_policy(
            AccountActionPolicyV1(
                id=f"account-policy.{environment_id}",
                version=1,
                environment_id=environment_id,
                evidence_refs=[reference],
                ai_identity_label=ai_label,
            )
        )
        inserted = 1
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.account-policy-initialize-result.v1",
            "inserted_policy_count": inserted,
            "policy": stored,
            "autonomous_boundary": "全部正常游戏内行为",
            "per_action_authorization_boundary": [
                "真实货币支付",
                "提交外部个人身份资料",
            ],
            "next_command": f"omni game player account policy --environment {environment_id}",
        },
        summary=f"账号策略已就绪：{environment_id}",
    )


@player_account.command("status")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_account_status(context: _PlayerCLIContext, environment_id: str) -> None:
    """查看账号、区服、当前阶段和预算，不触发游戏行为。"""

    projection = build_ai_player_console_projection(context.player(), environment_id=environment_id)
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.account-status.v1",
            "identity": projection.get("identity"),
            "current_state": projection.get("current_state"),
            "budget": projection.get("budget"),
            "daily_continuity": projection.get("daily_continuity"),
        },
        summary=f"账号环境：{environment_id}",
    )


@player_account.command("metric")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_account_metric(context: _PlayerCLIContext, environment_id: str) -> None:
    """查看账号指标推导及其 canonical 来源。"""

    metrics = context.player().list_account_metric_derivations(environment_id, limit=500)
    _emit(
        context,
        {"schema": "game-observatory.ai-player.account-metric-list.v1", "metrics": metrics},
        summary=f"账号指标记录：{len(metrics)} 条",
    )


@player_account.command("metric-derive")
@click.option("--environment", "environment_id", required=True)
@click.option("--source-step", "source_step_id", required=True)
@click.option(
    "--definition",
    "definition_path",
    required=True,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
)
@click.option(
    "--source",
    "source_kind",
    required=True,
    type=click.Choice(["screenshot-ocr", "authoritative-state"]),
)
@click.option("--before-region", type=(int, int, int, int), default=None)
@click.option("--after-region", type=(int, int, int, int), default=None)
@click.option("--before-artifact", "before_artifact_id", default=None)
@click.option("--after-artifact", "after_artifact_id", default=None)
@click.pass_obj
def player_account_metric_derive(
    context: _PlayerCLIContext,
    environment_id: str,
    source_step_id: str,
    definition_path: Path,
    source_kind: str,
    before_region: tuple[int, int, int, int] | None,
    after_region: tuple[int, int, int, int] | None,
    before_artifact_id: str | None,
    after_artifact_id: str | None,
) -> None:
    """从终态 EvidenceStep 的局部 OCR 或权威快照派生一项账号指标。"""

    try:
        definition = AccountMetricDefinitionV1.model_validate_json(definition_path.read_bytes())
        player = context.player()
        if source_kind == "screenshot-ocr":
            if before_region is None or after_region is None:
                raise ValueError("screenshot-ocr requires --before-region and --after-region")
            if before_artifact_id is not None or after_artifact_id is not None:
                raise ValueError("screenshot-ocr uses canonical Before/After frames directly")
            derivation, extraction_artifacts, inserted = extract_and_persist_screenshot_metric(
                player,
                environment_id=environment_id,
                evidence_step_id=source_step_id,
                definition=definition,
                before_region=SourcePixelRect(
                    x=before_region[0],
                    y=before_region[1],
                    width=before_region[2],
                    height=before_region[3],
                ),
                after_region=SourcePixelRect(
                    x=after_region[0],
                    y=after_region[1],
                    width=after_region[2],
                    height=after_region[3],
                ),
            )
            extraction_ids = [item.id for item in extraction_artifacts]
        else:
            if before_artifact_id is None or after_artifact_id is None:
                raise ValueError(
                    "authoritative-state requires --before-artifact and --after-artifact"
                )
            if before_region is not None or after_region is not None:
                raise ValueError("authoritative-state does not accept OCR regions")
            derivation, inserted = persist_authoritative_metric(
                player,
                environment_id=environment_id,
                evidence_step_id=source_step_id,
                definition=definition,
                before_artifact_id=before_artifact_id,
                after_artifact_id=after_artifact_id,
            )
            extraction_ids = []
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        context,
        {
            "schema": "game-observatory.ai-player.account-metric-derive-result.v1",
            "inserted": inserted,
            "derivation": derivation,
            "extraction_artifact_ids": extraction_ids,
            "source_policy": ("局部 OCR" if source_kind == "screenshot-ocr" else "权威结构化快照"),
        },
        summary=(
            f"账号指标 {derivation.definition.label}: "
            f"{derivation.delta.before} → {derivation.delta.after}"
        ),
    )


@player_account.command("policy")
@click.option("--environment", "environment_id", required=True)
@click.pass_obj
def player_account_policy(context: _PlayerCLIContext, environment_id: str) -> None:
    """查看支付、外部身份资料、游戏内行为和发言口径。"""

    policy = context.player().get_account_policy(environment_id)
    _emit(
        context,
        {"schema": "game-observatory.ai-player.account-policy-detail.v1", "policy": policy},
        summary=f"账号策略：{'已配置' if policy else '未配置'}",
    )


@cmd_player.group("console")
def player_console() -> None:
    """独立 AI 玩家控制台。"""


@player_console.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=click.IntRange(1, 65535), default=8222, show_default=True)
@click.option("--log-level", default="warning", show_default=True)
@click.pass_obj
def player_console_serve(
    context: _PlayerCLIContext,
    host: str,
    port: int,
    log_level: str,
) -> None:
    """前台启动本地控制台；页面位于 /game-observatory/console。"""

    os.environ["GAME_OBSERVATORY_ROOT"] = str(context.facility().store.root)
    try:
        import uvicorn

        from omnicompany.packages.domains.game_observatory.public_server import (
            create_public_app,
        )
    except ImportError as exc:
        raise click.ClickException(f"控制台运行依赖不可用: {exc}") from exc
    click.echo(
        f"AI 玩家控制台：http://{host}:{port}/game-observatory/console",
        err=True,
    )
    uvicorn.run(create_public_app(), host=host, port=port, log_level=log_level)


def _install_player_help_contract() -> None:
    """Append one consistent, discoverable operating contract to every player command."""

    cmd_player.epilog = (
        "最小示例：omni game player doctor\n\n"
        "风险：根命令不执行设备行为；具体写操作会在子命令中再次声明守卫。\n\n"
        "常用下一步：omni game player environment list"
    )
    next_commands = {
        "doctor": "omni game player environment list",
        "context": "omni game player session start --help",
        "environment": "omni game player context export --help",
        "device": "omni game player environment register --help",
        "session": "omni game player explore plan --help",
        "observe": "omni game player state current --help",
        "act": "omni game player evidence step --help",
        "explore": "omni game player session checkpoint --help",
        "task": "omni game player explore run --help",
        "state": "omni game player state route --help",
        "memory": "omni game player task list --help",
        "skill": "omni game player skill validate --help",
        "guide": "omni game player guide freshness --help",
        "evidence": "omni game player evidence export --help",
        "benchmark": "omni game player benchmark compare --help",
        "account": "omni game player account metric --help",
        "console": "http://127.0.0.1:8222/game-observatory/console",
    }
    device_paths = {
        "act tap",
        "act tap-preview",
        "act tap-anchor",
        "act tap-element",
        "act swipe",
        "act back",
        "act launch",
        "act text",
        "act wait",
        "explore run",
        "explore drive",
        "explore dispatch",
        "explore resume",
        "skill replay",
        "navigate",
    }
    provider_paths = {"session start", "session resume", "benchmark run"}
    mutation_groups = {
        "environment",
        "device",
        "session",
        "task",
        "memory",
        "skill",
        "guide",
        "evidence",
        "account",
        "console",
    }

    def visit(group: click.Group, prefix: tuple[str, ...] = ()) -> None:
        for name, command in group.commands.items():
            path = (*prefix, name)
            path_text = " ".join(path)
            for parameter in command.params:
                if isinstance(parameter, click.Option):
                    parameter.show_default = True
            required: list[str] = []
            for parameter in command.params:
                if isinstance(parameter, click.Argument) and parameter.required:
                    required.append(f"<{parameter.name.upper()}>")
                elif isinstance(parameter, click.Option) and parameter.required:
                    option = next(
                        (item for item in parameter.opts if item.startswith("--")),
                        parameter.opts[0],
                    )
                    required.append(f"{option} <{parameter.name.upper()}>")
            example = " ".join(["omni game player --json", path_text, *required]).strip()
            root = path[0]
            if path_text in device_paths:
                risk = (
                    "会触发游戏内行为；必须绑定运行中的 Session、来源 EvidenceStep、"
                    "预期变化和动作预算。"
                )
            elif path_text in provider_paths:
                risk = (
                    "会调用外部 Agent CLI；正常复杂决策按本轮 timeout 运行。仅在心跳消失、"
                    "持续无状态进展或设施异常时安全停止，并保留 Session。"
                )
            elif root == "observe" and len(path) > 1 and path[1] == "capture":
                risk = "只读设备画面，但会在本地保存一份截图和可用的 UI 树。"
            elif path_text == "environment register":
                risk = "会只读捕获当前设备画面，并追加一个不可变的本地环境身份。"
            elif path_text == "device list":
                risk = "仅 --refresh 会刷新本地设备发现记录；不会操作游戏。"
            elif path_text == "state review-export":
                risk = "只读 canonical 状态与证据，并在指定位置保存 hash 锁定的审查包。"
            elif path_text == "state review-apply":
                risk = "会追加独立审定后的状态和转移版本；原候选、证据与裁决种子保持不可变。"
            elif path_text == "state review-sign":
                risk = "读取独立审查者私钥并仅写签名 seed；不会操作设备或修改 canonical 状态。"
            elif root in mutation_groups and not path_text.endswith(
                (
                    " list",
                    " show",
                    " status",
                    " recall",
                    " search",
                    " freshness",
                    " run",
                    " step",
                    " metric",
                    " policy",
                )
            ):
                risk = "会追加或更新本地 canonical 状态；来源记录保持不可变并可审计。"
            else:
                risk = "只读命令，不触发设备行为。"
            next_command = next_commands.get(root, "omni game player --help")
            command.epilog = f"最小示例：{example}\n\n风险：{risk}\n\n常用下一步：{next_command}"
            if isinstance(command, click.Group):
                visit(command, path)

    visit(cmd_player)


_install_player_help_contract()
