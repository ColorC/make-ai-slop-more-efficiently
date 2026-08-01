from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from copy import deepcopy
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    StreamingResponse,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, model_validator

from .adapters import AdapterError
from .ai_player.console_projection import (
    build_ai_player_console_projection,
    build_path_reuse_health_projection,
)
from .ai_player.external_agent_runtime import ExternalAgentSessionLedger
from .ai_player.live_facility import (
    LiveFrameBroker,
    LiveInstructionStore,
    SpectatorInstructionSubmissionV1,
    build_live_room_projection,
)
from .ai_player.contracts import (
    AccountActionPolicyV1,
    GameplayCandidateV1,
    GuideKnowledgeV1,
    SpeechEventV1,
    SpeechIntentV1,
)
from .ai_player.session_control import (
    AIPlayerSessionCheckpointCommand,
    AIPlayerSessionCommand,
    AIPlayerSessionControl,
    AIPlayerSessionError,
    AIPlayerSessionHeartbeatCommand,
    AIPlayerSessionReconcileCommand,
    CreateAIPlayerSessionCommand,
)
from .ai_player.sanguo_daily_continuity import (
    RecordSanguoDailyDutyCommand,
    SanguoDailyContinuityError,
    SanguoDailyContinuityLedger,
    SanguoDailyStateCommand,
    SealSanguoDailyContinuityCommand,
)
from .ai_player.store import AIPlayerStore
from .ai_player.text_integrity import validate_canonical_text_payload
from .compiler import SemanticReportCompiler
from .gateway import DeviceGateway, GatewayError, LeaseConflict
from .models import (
    CommunityFeedbackItem,
    DemoReproduction,
    GameReport,
    DESIGN_SPEC_CONTRACT_V03,
    EvidenceDynamicSceneProfile,
    EvidenceRun,
    EvidenceAdjudicationLedger,
    EvidenceRunManifest,
    EvidenceStep,
    EvidenceTerminalCondition,
    NormalizedAction,
    PlayerVoice,
    ReverseEngineeredGameDesignSpec,
    SourcePixelRect,
    SourceRef,
)
from .reader_projection import build_reader_game_projection, build_reader_projection
from .reader_content_taxonomy import (
    reader_content_position,
    reader_content_taxonomy_manifest,
)
from .runtime import GameObservatory
from .saturation import load_saturation_ledger, validate_saturation_ledger
from .source_voice import SourceVoicePipeline
from .tag_taxonomy import (
    public_play_tag_details,
    public_play_tag_issues,
    public_play_tag_taxonomy,
    public_screen_tag_issues,
    public_tag_scopes,
)


game_observatory_router = APIRouter(tags=["game-observatory"])
_WEB = Path(__file__).resolve().parent / "web"
_PARTIAL_FACT_BUNDLE_SCHEMA = "game-observatory.partial-fact-bundle.v1"
_PUBLIC_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'; "
    "base-uri 'self'; form-action 'self'"
)


def _facility() -> GameObservatory:
    return GameObservatory()


@game_observatory_router.get("/api/game-observatory/tag-taxonomy")
def game_observatory_tag_taxonomy() -> dict[str, Any]:
    return {
        "schema": "game-observatory.public-tag-taxonomy.v1",
        "scopes": public_tag_scopes(),
        "play_tags": public_play_tag_taxonomy(),
    }


@game_observatory_router.get("/api/game-observatory/content-taxonomy")
def game_observatory_content_taxonomy() -> dict[str, Any]:
    return reader_content_taxonomy_manifest()


@game_observatory_router.get("/api/game-observatory/ai-player/console")
def ai_player_console_state(
    environment_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Read the selected AI-player environment from the canonical local store."""

    player = AIPlayerStore(_facility().store)
    try:
        return build_ai_player_console_projection(
            player,
            environment_id=environment_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_live_lan_client(request: Request) -> str:
    host = request.client.host if request.client else ""
    if host in {"127.0.0.1", "::1", "testclient"}:
        return host or "loopback"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="live room writes require a LAN client"
        ) from exc
    if not (address.is_private or address.is_link_local):
        raise HTTPException(
            status_code=403, detail="live room writes require a LAN client"
        )
    return host


@lru_cache(maxsize=4)
def _cached_live_frame_broker(observatory_root: str) -> LiveFrameBroker:
    facility = GameObservatory(Path(observatory_root))
    return LiveFrameBroker(AIPlayerStore(facility.store))


def _live_frame_broker() -> LiveFrameBroker:
    return _cached_live_frame_broker(str(_facility().store.root.resolve()))


@game_observatory_router.get("/api/game-observatory/ai-player/live")
def ai_player_live_room(
    environment_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
    player = AIPlayerStore(_facility().store)
    try:
        return build_live_room_projection(
            player,
            environment_id=environment_id,
            session_id=session_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/ai-player/live/instructions")
def submit_ai_player_live_instruction(
    body: SpectatorInstructionSubmissionV1,
    request: Request,
) -> dict[str, Any]:
    client_host = _require_live_lan_client(request)
    player = AIPlayerStore(_facility().store)
    if player.get_environment(body.environment_id) is None:
        raise HTTPException(status_code=404, detail="AI-player environment not found")
    if body.session_id is not None:
        external_session = ExternalAgentSessionLedger(
            player.observatory_store.root
        ).get_session(body.session_id)
        if (
            external_session is None
            or external_session.environment_id != body.environment_id
        ):
            raise HTTPException(
                status_code=404, detail="external Agent session not found"
            )
    try:
        item = LiveInstructionStore(player.observatory_store.root).submit(
            body,
            submitted_by=client_host,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "schema": "game-observatory.ai-player.live-instruction-result.v1",
        "instruction": item.model_dump(mode="json"),
        "delivery_policy": "next_provider_round_only",
    }


@game_observatory_router.get("/api/game-observatory/ai-player/live/frame.png")
def ai_player_live_frame(
    environment_id: str = Query(min_length=1),
) -> Response:
    try:
        frame = _live_frame_broker().frame(environment_id)
    except (AdapterError, KeyError, OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=frame.content,
        media_type=frame.media_type,
        headers={
            "Cache-Control": "no-store",
            "ETag": f'"{frame.sha256}"',
            "X-Captured-At": frame.captured_at,
            "X-Content-Type-Options": "nosniff",
        },
    )


@game_observatory_router.get("/api/game-observatory/ai-player/live/stream.mjpg")
def ai_player_live_stream(
    environment_id: str = Query(min_length=1),
    interval_seconds: float = Query(default=1.0, ge=0.5, le=5.0),
) -> StreamingResponse:
    try:
        _live_frame_broker().serial_for_environment(environment_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(
        _live_frame_broker().mjpeg(
            environment_id,
            interval_seconds=interval_seconds,
        ),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@game_observatory_router.get("/api/game-observatory/ai-player/live/stream/status")
def ai_player_live_stream_status(
    environment_id: str = Query(min_length=1),
) -> dict[str, Any]:
    try:
        legacy = _live_frame_broker().status(environment_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    distribution: dict[str, Any] = {
        "ready": False,
        "provider": "mediamtx",
        "path": "game-observatory",
        "target_fps": int(os.environ.get("OMNI_GAME_LIVE_TARGET_FPS", "60")),
        "target_resolution": "1920x1080",
        "video_codec": "H264",
        "publisher": "OBS WHIP / NVENC",
        "active_viewers": 0,
        "estimated_total_mbps": 0.0,
    }
    try:
        with urlopen(
            "http://127.0.0.1:9997/v3/paths/get/game-observatory",
            timeout=0.35,
        ) as response:
            path = json.loads(response.read())
        readers = path.get("readers") if isinstance(path.get("readers"), list) else []
        tracks = path.get("tracks") if isinstance(path.get("tracks"), list) else []
        distribution.update(
            {
                "ready": bool(path.get("ready")),
                "active_viewers": len(readers),
                "estimated_total_mbps": round(len(readers) * 12.0, 2),
                "tracks": tracks,
                "inbound_bytes": path.get("inboundBytes"),
                "outbound_bytes": path.get("outboundBytes"),
                "source": path.get("source"),
            }
        )
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        pass
    return {
        "schema": "game-observatory.ai-player.live-stream-status.v2",
        "mode": "webrtc" if distribution["ready"] else "mjpeg-fallback",
        "distribution": distribution,
        "legacy_fallback": legacy,
    }


@game_observatory_router.get("/api/game-observatory/ai-player/path-reuse-health")
def ai_player_path_reuse_health(
    environment_id: str = Query(min_length=1),
    detail_limit: int = Query(default=10, ge=0, le=40),
) -> dict[str, Any]:
    """Return the compact canonical projection used for reuse-aware scheduling."""

    player = AIPlayerStore(_facility().store)
    try:
        return build_path_reuse_health_projection(
            player,
            environment_id=environment_id,
            detail_limit=detail_limit,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@game_observatory_router.get("/api/game-observatory/ai-player/skills/{skill_id}")
def ai_player_skill_detail(
    skill_id: str,
    environment_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return one skill's immutable versions, runs, validations, and evidence."""

    player = AIPlayerStore(_facility().store)
    try:
        projection = build_ai_player_console_projection(
            player,
            environment_id=environment_id,
        )
        selection = projection.get("selection")
        selected_environment_id = (
            selection.get("selected_environment_id")
            if isinstance(selection, dict)
            else None
        )
        if not selected_environment_id:
            raise KeyError("no AI-player environment is available")
        versions = [
            item
            for item in player.list_skill_versions(
                selected_environment_id,
                latest_only=False,
            )
            if item.skill_id == skill_id
        ]
        if not versions:
            raise KeyError(f"unknown AI-player skill: {skill_id}")
        version_ids = {item.id for item in versions}
        runs = [
            item
            for item in player.list_skill_runs(selected_environment_id)
            if item.skill_version_id in version_ids
        ]
        validations = [
            item
            for item in player.list_skill_validations(selected_environment_id)
            if item.skill_version_id in version_ids
        ]
        return player.project_current_text_payload(
            {
                "schema": "game-observatory.ai-player.skill-detail.v1",
                "environment_id": selected_environment_id,
                "skill_id": skill_id,
                "versions": [
                    _ai_player_item(
                        player,
                        item,
                        source_table="ai_player_skill_versions",
                        key_fields=("environment_id", "id"),
                    )
                    for item in versions
                ],
                "runs": [
                    _ai_player_item(
                        player,
                        item,
                        source_table="ai_player_skill_runs",
                        key_fields=("environment_id", "id"),
                    )
                    for item in runs
                ],
                "validations": [
                    _ai_player_item(
                        player,
                        item,
                        source_table="ai_player_skill_validations",
                        key_fields=("environment_id", "id"),
                    )
                    for item in validations
                ],
            }
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _append_ai_player_contract(
    body: BaseModel,
    writer_name: str,
) -> dict[str, Any]:
    player = AIPlayerStore(_facility().store)
    writer = getattr(player, writer_name)
    try:
        validate_canonical_text_payload(
            body.model_dump(mode="json", by_alias=True),
            root_field="$.item",
        )
        stored = writer(body)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"item": stored.model_dump(mode="json", by_alias=True)}


def _ai_player_reader(environment_id: str) -> AIPlayerStore:
    player = AIPlayerStore(_facility().store)
    if player.get_environment(environment_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "environment_not_found",
                "message": "当前 AI 玩家环境不存在。",
            },
        )
    return player


def _ai_player_item_not_found(code: str, message: str) -> None:
    raise HTTPException(status_code=404, detail={"code": code, "message": message})


def _ai_player_items(
    player: AIPlayerStore,
    environment_id: str,
    items: list[BaseModel],
    *,
    source_table: str,
    key_fields: tuple[str, ...],
) -> dict[str, Any]:
    return player.project_current_text_payload(
        {
            "environment_id": environment_id,
            "items": [
                _ai_player_item(
                    player,
                    item,
                    source_table=source_table,
                    key_fields=key_fields,
                )
                for item in items
            ],
        }
    )


def _ai_player_item(
    player: AIPlayerStore,
    item: BaseModel,
    *,
    source_table: str,
    key_fields: tuple[str, ...],
) -> dict[str, Any]:
    payload = item.model_dump(mode="json", by_alias=True)
    return player.project_canonical_record_payload(
        payload,
        source_table=source_table,
        record_key={field: payload[field] for field in key_fields},
    )


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/reconcile-stale"
)
def reconcile_stale_ai_player_sessions(
    body: AIPlayerSessionReconcileCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Append lifecycle events that pause running sessions without a live worker lease."""

    _require_admin(request, x_game_observatory_token)
    control = _ai_player_session_control()
    try:
        sessions = control.reconcile_stale_sessions(body)
    except AIPlayerSessionError as exc:
        _raise_ai_player_session_error(exc)
    return control.player_store.project_current_text_payload(
        {
            "reconciled_count": len(sessions),
            "sessions": [
                _ai_player_session_payload(control, session) for session in sessions
            ],
        }
    )


@game_observatory_router.get("/api/game-observatory/ai-player/account-policies")
def list_ai_player_account_policies(
    environment_id: str = Query(min_length=1),
) -> dict[str, Any]:
    """Read every immutable version of the environment's canonical account policy."""

    player = _ai_player_reader(environment_id)
    latest = player.get_account_policy(environment_id)
    if latest is None:
        return _ai_player_items(
            player,
            environment_id,
            [],
            source_table="ai_player_account_policies",
            key_fields=("environment_id", "id", "version"),
        )
    policies = [
        policy
        for version in range(latest.version, 0, -1)
        if (
            policy := player.get_account_policy(
                environment_id,
                policy_id=latest.id,
                version=version,
            )
        )
        is not None
    ]
    return _ai_player_items(
        player,
        environment_id,
        policies,
        source_table="ai_player_account_policies",
        key_fields=("environment_id", "id", "version"),
    )


@game_observatory_router.get(
    "/api/game-observatory/ai-player/account-policies/{policy_id}"
)
def read_ai_player_account_policy(
    policy_id: str,
    environment_id: str = Query(min_length=1),
    version: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    policy = player.get_account_policy(
        environment_id,
        policy_id=policy_id,
        version=version,
    )
    if policy is None:
        _ai_player_item_not_found(
            "account_policy_not_found",
            "当前环境中没有这个账号行为策略版本。",
        )
    return player.project_current_text_payload(
        {
            "item": _ai_player_item(
                player,
                policy,
                source_table="ai_player_account_policies",
                key_fields=("environment_id", "id", "version"),
            )
        }
    )


@game_observatory_router.get("/api/game-observatory/ai-player/gameplay-candidates")
def list_ai_player_gameplay_candidates(
    environment_id: str = Query(min_length=1),
    latest_only: bool = Query(default=True),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    return _ai_player_items(
        player,
        environment_id,
        player.list_gameplay_candidates(environment_id, latest_only=latest_only),
        source_table="ai_player_gameplay_candidates",
        key_fields=("environment_id", "id", "version"),
    )


@game_observatory_router.get(
    "/api/game-observatory/ai-player/gameplay-candidates/{candidate_id}"
)
def read_ai_player_gameplay_candidate(
    candidate_id: str,
    environment_id: str = Query(min_length=1),
    version: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    candidate = player.get_gameplay_candidate(
        environment_id,
        candidate_id,
        version=version,
    )
    if candidate is None:
        _ai_player_item_not_found(
            "gameplay_candidate_not_found",
            "当前环境中没有这个玩法候选版本。",
        )
    return player.project_current_text_payload(
        {
            "item": _ai_player_item(
                player,
                candidate,
                source_table="ai_player_gameplay_candidates",
                key_fields=("environment_id", "id", "version"),
            )
        }
    )


@game_observatory_router.get("/api/game-observatory/ai-player/guide-knowledge")
def list_ai_player_guide_knowledge(
    environment_id: str = Query(min_length=1),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    return _ai_player_items(
        player,
        environment_id,
        player.list_guide_knowledge(environment_id),
        source_table="ai_player_guide_knowledge",
        key_fields=("environment_id", "id", "version"),
    )


@game_observatory_router.get(
    "/api/game-observatory/ai-player/guide-knowledge/{guide_id}"
)
def read_ai_player_guide_knowledge(
    guide_id: str,
    environment_id: str = Query(min_length=1),
    version: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    guide = player.get_guide_knowledge(environment_id, guide_id, version=version)
    if guide is None:
        _ai_player_item_not_found(
            "guide_knowledge_not_found",
            "当前环境中没有这个攻略知识版本。",
        )
    return player.project_current_text_payload(
        {
            "item": _ai_player_item(
                player,
                guide,
                source_table="ai_player_guide_knowledge",
                key_fields=("environment_id", "id", "version"),
            )
        }
    )


@game_observatory_router.get("/api/game-observatory/ai-player/speech-intents")
def list_ai_player_speech_intents(
    environment_id: str = Query(min_length=1),
    latest_only: bool = Query(default=True),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    return _ai_player_items(
        player,
        environment_id,
        player.list_speech_intents(environment_id, latest_only=latest_only),
        source_table="ai_player_speech_intents",
        key_fields=("environment_id", "id", "version"),
    )


@game_observatory_router.get(
    "/api/game-observatory/ai-player/speech-intents/{intent_id}"
)
def read_ai_player_speech_intent(
    intent_id: str,
    environment_id: str = Query(min_length=1),
    version: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    intent = player.get_speech_intent(environment_id, intent_id, version=version)
    if intent is None:
        _ai_player_item_not_found(
            "speech_intent_not_found",
            "当前环境中没有这个发言意图版本。",
        )
    return player.project_current_text_payload(
        {
            "item": _ai_player_item(
                player,
                intent,
                source_table="ai_player_speech_intents",
                key_fields=("environment_id", "id", "version"),
            )
        }
    )


@game_observatory_router.get("/api/game-observatory/ai-player/speech-events")
def list_ai_player_speech_events(
    environment_id: str = Query(min_length=1),
    speech_intent_id: str | None = Query(default=None, min_length=1),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    return _ai_player_items(
        player,
        environment_id,
        player.list_speech_events(
            environment_id,
            speech_intent_id=speech_intent_id,
        ),
        source_table="ai_player_speech_events",
        key_fields=("environment_id", "id"),
    )


@game_observatory_router.get("/api/game-observatory/ai-player/speech-events/{event_id}")
def read_ai_player_speech_event(
    event_id: str,
    environment_id: str = Query(min_length=1),
) -> dict[str, Any]:
    player = _ai_player_reader(environment_id)
    event = next(
        (
            item
            for item in player.list_speech_events(environment_id)
            if item.id == event_id
        ),
        None,
    )
    if event is None:
        _ai_player_item_not_found(
            "speech_event_not_found",
            "当前环境中没有这个发言结果。",
        )
    return player.project_current_text_payload(
        {
            "item": _ai_player_item(
                player,
                event,
                source_table="ai_player_speech_events",
                key_fields=("environment_id", "id"),
            )
        }
    )


@game_observatory_router.post("/api/game-observatory/ai-player/account-policies")
def append_ai_player_account_policy(
    body: AccountActionPolicyV1,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _append_ai_player_contract(body, "append_account_policy")


@game_observatory_router.post("/api/game-observatory/ai-player/gameplay-candidates")
def append_ai_player_gameplay_candidate(
    body: GameplayCandidateV1,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _append_ai_player_contract(body, "append_gameplay_candidate")


@game_observatory_router.post("/api/game-observatory/ai-player/guide-knowledge")
def append_ai_player_guide_knowledge(
    body: GuideKnowledgeV1,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _append_ai_player_contract(body, "append_guide_knowledge")


@game_observatory_router.post("/api/game-observatory/ai-player/speech-intents")
def append_ai_player_speech_intent(
    body: SpeechIntentV1,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _append_ai_player_contract(body, "append_speech_intent")


@game_observatory_router.post("/api/game-observatory/ai-player/speech-events")
def append_ai_player_speech_event(
    body: SpeechEventV1,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _append_ai_player_contract(body, "append_speech_event")


def _public_report(report: Any) -> dict[str, Any]:
    return SemanticReportCompiler.public_report(report)


def _request_role(request: Request, token: str | None) -> str | None:
    host = request.client.host if request.client else ""
    if host in {"127.0.0.1", "::1", "testclient"}:
        return "admin"
    mapping: dict[str, str] = {}
    try:
        raw = json.loads(os.environ.get("OMNI_GAME_OBSERVATORY_TOKENS", "{}"))
        if isinstance(raw, dict):
            mapping = {str(key): str(value) for key, value in raw.items()}
    except json.JSONDecodeError:
        mapping = {}
    legacy = os.environ.get("OMNI_GAME_OBSERVATORY_TOKEN")
    if legacy:
        mapping.setdefault(legacy, "admin")
    role = mapping.get(token or "")
    return role if role in {"author", "reviewer", "admin"} else None


def _require_editor(request: Request, token: str | None) -> str:
    role = _request_role(request, token)
    if role is None:
        raise HTTPException(status_code=403, detail="editor token required")
    return role


def _require_admin(request: Request, token: str | None) -> str:
    role = _request_role(request, token)
    if role != "admin":
        raise HTTPException(status_code=403, detail="admin token required")
    return role


def _require_reviewer(request: Request, token: str | None) -> str:
    role = _request_role(request, token)
    if role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="reviewer token required")
    return role


def _ai_player_session_control() -> AIPlayerSessionControl:
    return AIPlayerSessionControl(AIPlayerStore(_facility().store))


def _ai_player_session_payload(
    control: AIPlayerSessionControl,
    session: BaseModel,
) -> dict[str, Any]:
    return _ai_player_item(
        control.player_store,
        session,
        source_table="ai_player_sessions",
        key_fields=("id",),
    )


def _sanguo_daily_ledger() -> SanguoDailyContinuityLedger:
    return SanguoDailyContinuityLedger(AIPlayerStore(_facility().store))


def _raise_sanguo_daily_error(error: SanguoDailyContinuityError) -> None:
    raise HTTPException(
        status_code=409,
        detail={"code": error.code, "message": error.message},
    ) from error


def _raise_ai_player_session_error(error: AIPlayerSessionError) -> None:
    raise HTTPException(
        status_code=error.status_code, detail=error.as_detail()
    ) from error


@game_observatory_router.post("/api/game-observatory/ai-player/sessions")
def create_ai_player_session(
    body: CreateAIPlayerSessionCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Create a durable AI-player session without starting a device or game."""

    _require_admin(request, x_game_observatory_token)
    control = _ai_player_session_control()
    try:
        session = control.create_session(body)
    except AIPlayerSessionError as exc:
        _raise_ai_player_session_error(exc)
    return control.player_store.project_current_text_payload(
        {"session": _ai_player_session_payload(control, session)}
    )


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/{session_id}/heartbeat"
)
def heartbeat_ai_player_session(
    session_id: str,
    body: AIPlayerSessionHeartbeatCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Renew a canonical worker lease without invoking a device or gameplay adapter."""

    _require_admin(request, x_game_observatory_token)
    control = _ai_player_session_control()
    try:
        session = control.heartbeat(session_id, body)
    except AIPlayerSessionError as exc:
        _raise_ai_player_session_error(exc)
    return control.player_store.project_current_text_payload(
        {"session": _ai_player_session_payload(control, session)}
    )


@game_observatory_router.get("/api/game-observatory/ai-player/sessions")
def list_ai_player_sessions(
    environment_id: str = Query(min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    control = _ai_player_session_control()
    return control.player_store.project_current_text_payload(
        {
            "sessions": [
                _ai_player_session_payload(control, session)
                for session in control.list_sessions(environment_id, limit=limit)
            ]
        }
    )


@game_observatory_router.get("/api/game-observatory/ai-player/sessions/{session_id}")
def read_ai_player_session(
    session_id: str,
    environment_id: str = Query(min_length=1),
) -> dict[str, Any]:
    control = _ai_player_session_control()
    session = control.get_session(environment_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "session_not_found",
                "message": "当前环境中没有这个 AI 玩家会话。",
            },
        )
    return control.player_store.project_current_text_payload(
        {
            "session": _ai_player_session_payload(control, session),
            "events": [
                _ai_player_item(
                    control.player_store,
                    event,
                    source_table="ai_player_session_lifecycle_events",
                    key_fields=("id",),
                )
                for event in control.list_events(environment_id, session_id)
            ],
        }
    )


def _run_ai_player_session_command(
    session_id: str,
    operation: str,
    body: AIPlayerSessionCommand,
) -> dict[str, Any]:
    control = _ai_player_session_control()
    try:
        operation_method = getattr(control, operation)
        session = operation_method(session_id, body)
    except AIPlayerSessionError as exc:
        _raise_ai_player_session_error(exc)
    return control.player_store.project_current_text_payload(
        {"session": _ai_player_session_payload(control, session)}
    )


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/{session_id}/start"
)
def start_ai_player_session(
    session_id: str,
    body: AIPlayerSessionCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _run_ai_player_session_command(session_id, "start", body)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/{session_id}/pause"
)
def pause_ai_player_session(
    session_id: str,
    body: AIPlayerSessionCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _run_ai_player_session_command(session_id, "pause", body)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/{session_id}/resume"
)
def resume_ai_player_session(
    session_id: str,
    body: AIPlayerSessionCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _run_ai_player_session_command(session_id, "resume", body)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/{session_id}/safe-stop"
)
def safe_stop_ai_player_session(
    session_id: str,
    body: AIPlayerSessionCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _run_ai_player_session_command(session_id, "safe_stop", body)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/{session_id}/complete"
)
def complete_ai_player_session(
    session_id: str,
    body: AIPlayerSessionCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _run_ai_player_session_command(session_id, "complete", body)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sessions/{session_id}/checkpoint"
)
def checkpoint_ai_player_session(
    session_id: str,
    body: AIPlayerSessionCheckpointCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Persist orchestrator progress and evidence links without invoking a device."""

    _require_admin(request, x_game_observatory_token)
    control = _ai_player_session_control()
    try:
        session = control.checkpoint(session_id, body)
    except AIPlayerSessionError as exc:
        _raise_ai_player_session_error(exc)
    return control.player_store.project_current_text_payload(
        {"session": _ai_player_session_payload(control, session)}
    )


@game_observatory_router.get("/api/game-observatory/ai-player/sanguo/daily-continuity")
def list_sanguo_daily_continuity_runs(
    environment_id: str = Query(min_length=1),
) -> dict[str, Any]:
    ledger = _sanguo_daily_ledger()
    try:
        run_ids = ledger.list_run_ids(environment_id)
    except SanguoDailyContinuityError as exc:
        _raise_sanguo_daily_error(exc)
    return {
        "environment_id": environment_id,
        "runs": [
            ledger.schedule(environment_id, run_id).model_dump(
                mode="json", by_alias=True
            )
            for run_id in run_ids
        ],
    }


@game_observatory_router.get(
    "/api/game-observatory/ai-player/sanguo/daily-continuity/{continuity_run_id}"
)
def read_sanguo_daily_continuity_run(
    continuity_run_id: str,
    environment_id: str = Query(min_length=1),
) -> dict[str, Any]:
    ledger = _sanguo_daily_ledger()
    try:
        days = ledger.list_days(environment_id, continuity_run_id)
        schedule = ledger.schedule(environment_id, continuity_run_id)
        assessment = ledger.assess(environment_id, continuity_run_id)
    except SanguoDailyContinuityError as exc:
        _raise_sanguo_daily_error(exc)
    return {
        "days": [day.model_dump(mode="json", by_alias=True) for day in days],
        "schedule": schedule.model_dump(mode="json", by_alias=True),
        "assessment": assessment.model_dump(mode="json", by_alias=True),
    }


def _daily_mutation_result(operation: str, body: BaseModel) -> dict[str, Any]:
    ledger = _sanguo_daily_ledger()
    try:
        day = getattr(ledger, operation)(body)
    except SanguoDailyContinuityError as exc:
        _raise_sanguo_daily_error(exc)
    return {
        "day": day.model_dump(mode="json", by_alias=True),
        "schedule": ledger.schedule(
            day.environment_id, day.continuity_run_id
        ).model_dump(mode="json", by_alias=True),
    }


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sanguo/daily-continuity/duties"
)
def record_sanguo_daily_duty(
    body: RecordSanguoDailyDutyCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _daily_mutation_result("record_duty", body)


def _daily_state_mutation(
    operation: str,
    body: SanguoDailyStateCommand,
    request: Request,
    token: str | None,
) -> dict[str, Any]:
    _require_admin(request, token)
    return _daily_mutation_result(operation, body)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sanguo/daily-continuity/interrupt"
)
def interrupt_sanguo_daily_continuity(
    body: SanguoDailyStateCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    return _daily_state_mutation("interrupt", body, request, x_game_observatory_token)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sanguo/daily-continuity/resume"
)
def resume_sanguo_daily_continuity(
    body: SanguoDailyStateCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    return _daily_state_mutation("resume", body, request, x_game_observatory_token)


@game_observatory_router.post(
    "/api/game-observatory/ai-player/sanguo/daily-continuity/seal"
)
def seal_sanguo_daily_continuity(
    body: SealSanguoDailyContinuityCommand,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    return _daily_mutation_result("seal", body)


def _etag(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )
    return f'"{hashlib.sha256(encoded).hexdigest()}"'


def _asset_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@lru_cache(maxsize=256)
def _internal_thumbnail_bytes(
    path: str,
    source_sha256: str,
    max_width: int,
    max_height: int,
) -> bytes:
    """Decode one immutable canonical image into a bounded in-memory thumbnail."""

    del (
        source_sha256
    )  # It remains part of the cache key and invalidates changed content.
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        output = BytesIO()
        image.save(output, format="WEBP", quality=80, method=4)
    return output.getvalue()


def _public_site_document() -> str:
    value = (_WEB / "index.html").read_text(encoding="utf-8")
    for name in ("styles.css", "app.js"):
        value = value.replace(
            f"/game-observatory/{name}",
            f"/game-observatory/{name}?v={_asset_version(_WEB / name)}",
        )
    return value


def _studio_document() -> str:
    value = (_WEB / "studio.html").read_text(encoding="utf-8")
    for name in ("studio.css", "studio.js"):
        value = value.replace(
            f"/game-observatory/{name}",
            f"/game-observatory/{name}?v={_asset_version(_WEB / name)}",
        )
    return value


def _live_document() -> str:
    value = (_WEB / "live.html").read_text(encoding="utf-8")
    for name in ("live.css", "live.js"):
        value = value.replace(
            f"/game-observatory/{name}",
            f"/game-observatory/{name}?v={_asset_version(_WEB / name)}",
        )
    return value


def _partial_fact_drafts_root(facility: GameObservatory) -> Path:
    return (facility.store.root / "drafts").resolve()


def _partial_fact_path(facility: GameObservatory, relative_path: str) -> Path:
    """Resolve one JSON draft without allowing an escape from the drafts directory."""
    value = relative_path.strip()
    candidate = Path(value)
    normalized_parts = Path(value.replace("\\", "/")).parts
    if (
        not value
        or "\x00" in value
        or candidate.is_absolute()
        or bool(candidate.drive)
        or ".." in normalized_parts
        or candidate.suffix.lower() != ".json"
    ):
        raise ValueError("invalid partial fact bundle path")
    root = _partial_fact_drafts_root(facility)
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("partial fact bundle path escapes drafts directory") from exc
    return resolved


_DERIVED_PARTIAL_FACT_FORBIDDEN_FIELDS = {
    "game",
    "build",
    "platform",
    "screen_families",
    "screen_states",
    "screen_tags",
    "ui_elements",
    "interactions",
    "state_transitions",
    "visible_mechanics",
    "resource_displays",
    "source_refs",
    "play_records",
    "community_feedback",
    "demo_reproductions",
    "evidence_gaps",
    "evidence_run_id",
    "evidence_run_ids",
    "play_tag_previews",
    "play_connections",
    "interpretations",
    "boundary_manifest",
}


def _partial_fact_string_selector(
    partition: dict[str, Any],
    field: str,
    *,
    required: bool = False,
) -> list[str]:
    if field not in partition:
        if required:
            raise ValueError(
                f"derived partial fact bundle requires content_partition.{field}"
            )
        return []
    values = partition[field]
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ValueError(
            f"derived partial fact bundle content_partition.{field} must be string ids"
        )
    normalized = [value.strip() for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError(
            f"derived partial fact bundle content_partition.{field} contains duplicates"
        )
    if required and not normalized:
        raise ValueError(
            f"derived partial fact bundle content_partition.{field} must not be empty"
        )
    return normalized


def _partial_fact_ids(items: Any) -> set[str]:
    if not isinstance(items, list):
        return set()
    return {
        str(item.get("id"))
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }


def _validate_partial_fact_selector(
    field: str,
    selected_ids: list[str],
    available_ids: set[str],
) -> None:
    unknown = sorted(set(selected_ids) - available_ids)
    if unknown:
        raise ValueError(
            f"derived partial fact bundle content_partition.{field} references unknown ids: "
            f"{unknown}"
        )


_PARTIAL_FACT_FAMILY_OVERRIDE_FIELDS = {
    "title",
    "summary",
    "information_blocks",
    "interaction_rules",
}


def _partial_fact_family_overrides(
    partition: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if "family_overrides" not in partition:
        return {}
    raw_overrides = partition["family_overrides"]
    if not isinstance(raw_overrides, dict):
        raise ValueError(
            "derived partial fact bundle content_partition.family_overrides must be an object"
        )

    overrides: dict[str, dict[str, Any]] = {}
    for family_id, raw_override in raw_overrides.items():
        if not isinstance(family_id, str) or not family_id.strip():
            raise ValueError(
                "derived partial fact bundle content_partition.family_overrides keys "
                "must be non-empty family ids"
            )
        if family_id != family_id.strip():
            raise ValueError(
                "derived partial fact bundle content_partition.family_overrides keys "
                "must not contain surrounding whitespace"
            )
        if not isinstance(raw_override, dict):
            raise ValueError(
                "derived partial fact bundle content_partition.family_overrides values "
                "must be objects"
            )
        unknown_fields = sorted(
            set(raw_override) - _PARTIAL_FACT_FAMILY_OVERRIDE_FIELDS
        )
        if unknown_fields:
            raise ValueError(
                "derived partial fact bundle content_partition.family_overrides "
                f"contains unknown fields for {family_id}: {unknown_fields}"
            )
        for field, field_value in raw_override.items():
            if field in {"title", "summary"} and not isinstance(field_value, str):
                raise ValueError(
                    "derived partial fact bundle content_partition.family_overrides "
                    f"{family_id}.{field} must be a string"
                )
            if field == "information_blocks" and (
                not isinstance(field_value, list)
                or any(not isinstance(item, str) for item in field_value)
            ):
                raise ValueError(
                    "derived partial fact bundle content_partition.family_overrides "
                    f"{family_id}.{field} must be a list of strings"
                )
            if field == "interaction_rules" and (
                not isinstance(field_value, list)
                or any(not isinstance(item, dict) for item in field_value)
            ):
                raise ValueError(
                    "derived partial fact bundle content_partition.family_overrides "
                    f"{family_id}.{field} must be a list of objects"
                )
        overrides[family_id] = deepcopy(raw_override)
    return overrides


def _materialize_derived_partial_fact_bundle(
    value: dict[str, Any],
    path: Path,
    facility: GameObservatory | None,
    load_cache: dict[Path, dict[str, Any]] | None,
) -> dict[str, Any]:
    if facility is None:
        raise ValueError(
            "derived partial fact bundle requires a Game Observatory facility"
        )

    required_fields = (
        "id",
        "status",
        "publication_ready",
        "base_bundle_path",
        "base_bundle_id",
        "play",
        "scope",
        "design_document",
        "content_partition",
    )
    for field in required_fields:
        if field not in value:
            raise ValueError(f"derived partial fact bundle requires {field}")
    if not isinstance(value.get("publication_ready"), bool):
        raise ValueError(
            "derived partial fact bundle publication_ready must be a boolean"
        )
    for field in ("id", "status", "base_bundle_path", "base_bundle_id"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"derived partial fact bundle requires non-empty {field}")
    for field in ("play", "scope", "design_document", "content_partition"):
        if not isinstance(value.get(field), dict):
            raise ValueError(f"derived partial fact bundle {field} must be an object")

    forbidden = sorted(_DERIVED_PARTIAL_FACT_FORBIDDEN_FIELDS & set(value))
    if forbidden:
        raise ValueError(
            "derived partial fact bundle must not duplicate canonical fact fields: "
            f"{forbidden}"
        )

    base_relative_path = value["base_bundle_path"].strip()
    base_path = _partial_fact_path(facility, base_relative_path)
    if base_path == path.resolve():
        raise ValueError("derived partial fact bundle cannot reference itself")
    try:
        base_raw = json.loads(base_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"derived partial fact base bundle cannot be read: {exc}"
        ) from exc
    if (
        not isinstance(base_raw, dict)
        or base_raw.get("schema") != _PARTIAL_FACT_BUNDLE_SCHEMA
    ):
        raise ValueError(
            f"derived partial fact base schema must be {_PARTIAL_FACT_BUNDLE_SCHEMA}"
        )
    if base_raw.get("base_bundle_path"):
        raise ValueError("derived partial fact bundle chains are not supported")
    if str(base_raw.get("id") or "") != value["base_bundle_id"].strip():
        raise ValueError(
            "derived partial fact bundle base_bundle_id does not match its base"
        )

    base = _load_partial_fact_bundle(base_path, facility, _cache=load_cache)
    partition = value["content_partition"]
    evidence_empty_candidate = partition.get("evidence_empty_candidate", False)
    if not isinstance(evidence_empty_candidate, bool):
        raise ValueError(
            "derived partial fact bundle content_partition.evidence_empty_candidate "
            "must be a boolean"
        )
    surface_ids = _partial_fact_string_selector(
        partition,
        "strict_surface_ids",
        required=not evidence_empty_candidate,
    )
    mechanic_ids = _partial_fact_string_selector(partition, "strict_mechanic_ids")
    record_ids = _partial_fact_string_selector(partition, "strict_play_record_ids")
    gap_ids = _partial_fact_string_selector(partition, "strict_evidence_gap_ids")
    feedback_ids = _partial_fact_string_selector(partition, "game_feedback_ids")
    ui_ids = _partial_fact_string_selector(partition, "strict_ui_element_ids")
    interaction_ids = _partial_fact_string_selector(partition, "strict_interaction_ids")
    transition_ids = _partial_fact_string_selector(partition, "strict_transition_ids")
    resource_ids = _partial_fact_string_selector(partition, "strict_resource_ids")
    source_ref_ids = _partial_fact_string_selector(partition, "strict_source_ref_ids")
    explicit_ui_selector = "strict_ui_element_ids" in partition
    explicit_interaction_selector = "strict_interaction_ids" in partition
    explicit_transition_selector = "strict_transition_ids" in partition
    explicit_resource_selector = "strict_resource_ids" in partition
    explicit_source_ref_selector = "strict_source_ref_ids" in partition
    family_overrides = _partial_fact_family_overrides(partition)

    _validate_partial_fact_selector(
        "strict_surface_ids", surface_ids, _partial_fact_ids(base.get("screen_states"))
    )
    _validate_partial_fact_selector(
        "strict_mechanic_ids",
        mechanic_ids,
        _partial_fact_ids(base.get("visible_mechanics")),
    )
    _validate_partial_fact_selector(
        "strict_play_record_ids",
        record_ids,
        _partial_fact_ids(base.get("play_records")),
    )
    _validate_partial_fact_selector(
        "strict_evidence_gap_ids", gap_ids, _partial_fact_ids(base.get("evidence_gaps"))
    )
    _validate_partial_fact_selector(
        "game_feedback_ids",
        feedback_ids,
        _partial_fact_ids(base.get("community_feedback")),
    )
    _validate_partial_fact_selector(
        "strict_ui_element_ids", ui_ids, _partial_fact_ids(base.get("ui_elements"))
    )
    _validate_partial_fact_selector(
        "strict_interaction_ids",
        interaction_ids,
        _partial_fact_ids(base.get("interactions")),
    )
    _validate_partial_fact_selector(
        "strict_transition_ids",
        transition_ids,
        _partial_fact_ids(base.get("state_transitions")),
    )
    _validate_partial_fact_selector(
        "strict_resource_ids",
        resource_ids,
        _partial_fact_ids(base.get("resource_displays")),
    )
    _validate_partial_fact_selector(
        "strict_source_ref_ids",
        source_ref_ids,
        _partial_fact_ids(base.get("source_refs")),
    )
    unknown_override_families = sorted(
        set(family_overrides) - _partial_fact_ids(base.get("screen_families"))
    )
    if unknown_override_families:
        raise ValueError(
            "derived partial fact bundle content_partition.family_overrides references "
            f"unknown family ids: {unknown_override_families}"
        )

    selected_surfaces = set(surface_ids)
    selected_mechanics = set(mechanic_ids)
    selected_records = set(record_ids)
    selected_gaps = set(gap_ids)
    selected_feedback = set(feedback_ids)
    selected_ui = set(ui_ids)
    selected_interactions = set(interaction_ids)
    selected_transitions = set(transition_ids)
    selected_resources = set(resource_ids)
    selected_source_refs = set(source_ref_ids)

    if evidence_empty_candidate:
        if value.get("publication_ready"):
            raise ValueError(
                "derived partial fact bundle evidence_empty_candidate cannot be published"
            )
        selected_fact_ids = (
            selected_surfaces
            | selected_mechanics
            | selected_records
            | selected_feedback
            | selected_ui
            | selected_interactions
            | selected_transitions
            | selected_resources
            | selected_source_refs
        )
        if selected_fact_ids:
            raise ValueError(
                "derived partial fact bundle evidence_empty_candidate cannot borrow fact ids"
            )
        if not selected_gaps:
            raise ValueError(
                "derived partial fact bundle evidence_empty_candidate requires an explicit "
                "evidence gap"
            )

    projected_states = [
        deepcopy(item)
        for item in base.get("screen_states", [])
        if isinstance(item, dict) and str(item.get("id")) in selected_surfaces
    ]
    state_artifact_ids = [
        str(artifact_id)
        for item in projected_states
        for artifact_id in item.get("artifact_ids", [])
        if str(artifact_id).strip()
    ]
    state_artifact_id_set = set(state_artifact_ids)

    projected_families: list[dict[str, Any]] = []
    for family in base.get("screen_families", []):
        if not isinstance(family, dict):
            continue
        family_state_ids = [
            str(state_id)
            for state_id in family.get("screen_state_ids", [])
            if str(state_id) in selected_surfaces
        ]
        if not family_state_ids:
            continue
        projected = deepcopy(family)
        projected["screen_state_ids"] = family_state_ids
        gallery = [
            str(artifact_id)
            for artifact_id in family.get("gallery_artifact_ids", [])
            if str(artifact_id) in state_artifact_id_set
        ]
        projected["gallery_artifact_ids"] = gallery
        representative = str(family.get("representative_artifact_id") or "")
        if representative not in state_artifact_id_set:
            representative = next(
                (
                    artifact_id
                    for state_id in family_state_ids
                    for state in projected_states
                    if str(state.get("id")) == state_id
                    for artifact_id in state.get("artifact_ids", [])
                    if str(artifact_id).strip()
                ),
                "",
            )
        projected["representative_artifact_id"] = representative
        if representative and representative not in projected["gallery_artifact_ids"]:
            projected["gallery_artifact_ids"].insert(0, representative)
        projected_families.append(projected)

    projected_family_ids = _partial_fact_ids(projected_families)
    non_projected_override_families = sorted(
        set(family_overrides) - projected_family_ids
    )
    if non_projected_override_families:
        raise ValueError(
            "derived partial fact bundle content_partition.family_overrides references "
            "families outside the final projection: "
            f"{non_projected_override_families}"
        )
    for family in projected_families:
        for field, field_value in family_overrides.get(
            str(family.get("id")), {}
        ).items():
            family[field] = deepcopy(field_value)

    projected_elements: list[dict[str, Any]] = []
    for item in base.get("ui_elements", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id"))
        if explicit_ui_selector and item_id not in selected_ui:
            continue
        declared_surfaces = {
            str(state_id) for state_id in item.get("screen_state_ids", [])
        }
        if explicit_ui_selector and not (declared_surfaces & selected_surfaces):
            raise ValueError(
                "derived partial fact bundle content_partition.strict_ui_element_ids "
                f"selects {item_id} outside strict_surface_ids"
            )
        item_surfaces = [
            str(state_id)
            for state_id in item.get("screen_state_ids", [])
            if str(state_id) in selected_surfaces
        ]
        if not item_surfaces:
            continue
        projected = deepcopy(item)
        projected["screen_state_ids"] = item_surfaces
        projected_elements.append(projected)

    projected_interactions: list[dict[str, Any]] = []
    for item in base.get("interactions", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id"))
        if explicit_interaction_selector and item_id not in selected_interactions:
            continue
        endpoints = {
            str(item.get("from_state_id")),
            str(item.get("to_state_id")),
        }
        closed = "" not in endpoints and endpoints <= selected_surfaces
        if explicit_interaction_selector and not closed:
            raise ValueError(
                "derived partial fact bundle content_partition.strict_interaction_ids "
                f"selects {item_id} outside strict_surface_ids"
            )
        if closed:
            projected_interactions.append(deepcopy(item))
    projected_interaction_ids = _partial_fact_ids(projected_interactions)
    projected_transitions: list[dict[str, Any]] = []
    for item in base.get("state_transitions", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id"))
        if explicit_transition_selector and item_id not in selected_transitions:
            continue
        endpoints = {
            str(item.get("from_state_id")),
            str(item.get("to_state_id")),
        }
        closed = "" not in endpoints and endpoints <= selected_surfaces
        if explicit_transition_selector and not closed:
            raise ValueError(
                "derived partial fact bundle content_partition.strict_transition_ids "
                f"selects {item_id} outside strict_surface_ids"
            )
        if not closed:
            continue
        projected = deepcopy(item)
        declared_interactions = {
            str(interaction_id)
            for interaction_id in item.get("via_interaction_ids", [])
        }
        if (
            explicit_transition_selector
            and not declared_interactions <= projected_interaction_ids
        ):
            missing_interactions = sorted(
                declared_interactions - projected_interaction_ids
            )
            raise ValueError(
                "derived partial fact bundle content_partition.strict_transition_ids "
                f"selects {item_id} with via_interaction_ids outside the final "
                f"interaction projection: {missing_interactions}"
            )
        if not explicit_transition_selector:
            projected["via_interaction_ids"] = [
                str(interaction_id)
                for interaction_id in item.get("via_interaction_ids", [])
                if str(interaction_id) in projected_interaction_ids
            ]
        projected_transitions.append(projected)

    projected_resources: list[dict[str, Any]] = []
    for item in base.get("resource_displays", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id"))
        if explicit_resource_selector and item_id not in selected_resources:
            continue
        screen_state_id = str(item.get("screen_state_id"))
        closed = bool(screen_state_id) and screen_state_id in selected_surfaces
        if explicit_resource_selector and not closed:
            raise ValueError(
                "derived partial fact bundle content_partition.strict_resource_ids "
                f"selects {item_id} outside strict_surface_ids"
            )
        if closed:
            projected_resources.append(deepcopy(item))

    projected_source_refs = [
        deepcopy(item)
        for item in base.get("source_refs", [])
        if isinstance(item, dict)
        and (
            not explicit_source_ref_selector
            or str(item.get("id")) in selected_source_refs
        )
    ]

    projected_mechanics: list[dict[str, Any]] = []
    for item in base.get("visible_mechanics", []):
        if not isinstance(item, dict) or str(item.get("id")) not in selected_mechanics:
            continue
        projected = deepcopy(item)
        projected["screen_state_ids"] = [
            str(state_id)
            for state_id in item.get("screen_state_ids", [])
            if str(state_id) in selected_surfaces
        ]
        if not projected["screen_state_ids"]:
            raise ValueError(
                "derived partial fact selected mechanic has no selected surface: "
                f"{item.get('id')}"
            )
        projected_mechanics.append(projected)

    projected_records = [
        deepcopy(item)
        for item in base.get("play_records", [])
        if isinstance(item, dict) and str(item.get("id")) in selected_records
    ]
    projected_demos = [
        deepcopy(item)
        for item in base.get("demo_reproductions", [])
        if isinstance(item, dict)
        and bool(item.get("covered_surface_ids") or item.get("covered_interaction_ids"))
        and set(str(value) for value in item.get("covered_surface_ids", []))
        <= selected_surfaces
        and set(str(value) for value in item.get("covered_interaction_ids", []))
        <= projected_interaction_ids
    ]

    materialized = {
        "schema": _PARTIAL_FACT_BUNDLE_SCHEMA,
        "id": value["id"],
        "status": value["status"],
        "publication_ready": value["publication_ready"],
        "content_kind": "play",
        "coverage_claim": "play-boundary",
        "base_bundle_path": base_relative_path,
        "base_bundle_id": value["base_bundle_id"],
        "boundary_manifest": deepcopy(base.get("boundary_manifest", "")),
        "game": deepcopy(base.get("game", {})),
        "play": deepcopy(value["play"]),
        "scope": deepcopy(value["scope"]),
        "build": deepcopy(base.get("build", {})),
        "platform": deepcopy(base.get("platform", {})),
        "design_document": deepcopy(value["design_document"]),
        "content_partition": deepcopy(partition),
        "screen_families": projected_families,
        "screen_states": projected_states,
        "screen_tags": [
            deepcopy(item)
            for item in base.get("screen_tags", [])
            if isinstance(item, dict)
            and str(item.get("screen_state_id")) in selected_surfaces
        ],
        "ui_elements": projected_elements,
        "interactions": projected_interactions,
        "state_transitions": projected_transitions,
        "visible_mechanics": projected_mechanics,
        "resource_displays": projected_resources,
        "source_refs": projected_source_refs,
        "play_tag_previews": [],
        "community_feedback": [
            deepcopy(item)
            for item in base.get("community_feedback", [])
            if isinstance(item, dict) and str(item.get("id")) in selected_feedback
        ],
        "play_records": projected_records,
        "demo_reproductions": projected_demos,
        "evidence_gaps": [
            deepcopy(item)
            for item in base.get("evidence_gaps", [])
            if isinstance(item, dict) and str(item.get("id")) in selected_gaps
        ],
        "resolution": {
            "kind": "derived-play-view",
            "view_path": path.resolve()
            .relative_to(_partial_fact_drafts_root(facility))
            .as_posix(),
            "base_bundle_path": base_relative_path,
            "base_bundle_id": value["base_bundle_id"],
        },
    }

    record_run_ids = [
        str(item.get("evidence_run_id"))
        for item in projected_records
        if str(item.get("evidence_run_id") or "").strip()
    ]
    step_ids = sorted(_partial_fact_evidence_step_ids(materialized))
    step_by_id = facility.store.get_evidence_steps(step_ids)
    referenced_run_ids = {
        step.evidence_run_id for step in step_by_id.values() if step.evidence_run_id
    } | set(record_run_ids)
    base_run_ids = [
        str(run_id)
        for run_id in [base.get("evidence_run_id"), *base.get("evidence_run_ids", [])]
        if str(run_id or "").strip()
    ]
    ordered_run_ids = list(
        dict.fromkeys(
            [
                *record_run_ids,
                *(run_id for run_id in base_run_ids if run_id in referenced_run_ids),
                *sorted(referenced_run_ids),
            ]
        )
    )
    materialized["evidence_run_ids"] = ordered_run_ids
    materialized["evidence_run_id"] = ordered_run_ids[0] if ordered_run_ids else ""
    return materialized


def _load_partial_fact_bundle(
    path: Path,
    facility: GameObservatory | None = None,
    *,
    _cache: dict[Path, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cache_key = path.resolve()
    if _cache is not None and cache_key in _cache:
        # The cache is request-scoped and projections copy every selected object before
        # changing it. Reusing the validated base by reference avoids copying the whole
        # shared fact graph once per derived view.
        return _cache[cache_key]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"partial fact bundle cannot be read: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != _PARTIAL_FACT_BUNDLE_SCHEMA
    ):
        raise ValueError(
            f"partial fact bundle schema must be {_PARTIAL_FACT_BUNDLE_SCHEMA}"
        )
    if value.get("base_bundle_path") is not None:
        value = _materialize_derived_partial_fact_bundle(value, path, facility, _cache)
    play = value.get("play")
    if not isinstance(play, dict):
        raise ValueError("partial fact bundle play must be an object")
    tag_issues = public_play_tag_issues(play.get("tags"))
    if tag_issues:
        raise ValueError(
            "partial fact bundle public play tags are invalid: " + "; ".join(tag_issues)
        )
    screen_tag_issues = public_screen_tag_issues(value.get("screen_tags", []))
    if screen_tag_issues:
        raise ValueError(
            "partial fact bundle public screen tags are invalid: "
            + "; ".join(screen_tag_issues)
        )
    feedback = value.get("community_feedback", [])
    if not isinstance(feedback, list):
        raise ValueError("partial fact bundle community_feedback must be a list")
    try:
        value["community_feedback"] = [
            CommunityFeedbackItem.model_validate(item).model_dump(mode="json")
            for item in feedback
        ]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"partial fact bundle community feedback is invalid: {exc}"
        ) from exc
    source_refs = value.get("source_refs", [])
    if not isinstance(source_refs, list):
        raise ValueError("partial fact bundle source_refs must be a list")
    source_ref_by_id: dict[str, dict[str, Any]] = {}
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            raise ValueError("partial fact bundle source ref must be an object")
        for field in ("id", "kind", "title", "url"):
            if (
                not isinstance(source_ref.get(field), str)
                or not source_ref[field].strip()
            ):
                raise ValueError(f"partial fact bundle source ref requires {field}")
        source_ref_id = source_ref["id"]
        if source_ref_id in source_ref_by_id:
            raise ValueError(
                f"partial fact bundle source ref id is duplicated: {source_ref_id}"
            )
        source_ref_by_id[source_ref_id] = source_ref
    evidence_run_ids = value.get("evidence_run_ids", [])
    if not isinstance(evidence_run_ids, list):
        raise ValueError("partial fact bundle evidence_run_ids must be a list")
    declared_evidence_run_ids = {
        str(run_id)
        for run_id in [value.get("evidence_run_id"), *evidence_run_ids]
        if str(run_id or "").strip()
    }
    play_records = value.get("play_records", [])
    if not isinstance(play_records, list):
        raise ValueError("partial fact bundle play_records must be a list")
    seen_play_record_ids: set[str] = set()
    for record in play_records:
        if not isinstance(record, dict):
            raise ValueError("partial fact bundle play record must be an object")
        for field in ("id", "source_type", "title", "platform", "captured_on"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise ValueError(f"partial fact bundle play record requires {field}")
        record_id = record["id"]
        if record_id in seen_play_record_ids:
            raise ValueError(
                f"partial fact bundle play record id is duplicated: {record_id}"
            )
        seen_play_record_ids.add(record_id)
        source_type = record["source_type"]
        if source_type not in {"ai_player_live_run", "human_screen_recording"}:
            raise ValueError(
                f"partial fact bundle play record {record_id} has unknown source_type"
            )
        record_source_ids = record.get("source_ids", [])
        record_artifact_ids = record.get("artifact_ids", [])
        if not isinstance(record_source_ids, list) or any(
            not isinstance(source_id, str) or not source_id.strip()
            for source_id in record_source_ids
        ):
            raise ValueError(
                f"partial fact bundle play record {record_id} source_ids must be string ids"
            )
        if not isinstance(record_artifact_ids, list) or any(
            not isinstance(artifact_id, str) or not artifact_id.strip()
            for artifact_id in record_artifact_ids
        ):
            raise ValueError(
                f"partial fact bundle play record {record_id} artifact_ids must be string ids"
            )
        if source_type == "ai_player_live_run":
            run_id = record.get("evidence_run_id")
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError(
                    f"partial fact bundle AI play record {record_id} requires evidence_run_id"
                )
            if facility is not None and facility.store.get_evidence_run(run_id) is None:
                raise ValueError(
                    f"partial fact bundle AI play record {record_id} references unknown "
                    f"EvidenceRun: {run_id}"
                )
            if run_id not in declared_evidence_run_ids:
                raise ValueError(
                    f"partial fact bundle AI play record {record_id} EvidenceRun is not "
                    f"declared for this play bundle: {run_id}"
                )
        else:
            if not (record_source_ids or record_artifact_ids):
                raise ValueError(
                    f"partial fact bundle human recording {record_id} requires source_ids "
                    "or artifact_ids"
                )
            missing_sources = sorted(set(record_source_ids) - set(source_ref_by_id))
            if missing_sources:
                raise ValueError(
                    f"partial fact bundle human recording {record_id} references unknown "
                    f"source_refs: {missing_sources}"
                )
            if facility is not None:
                record_artifacts = facility.store.get_artifacts(record_artifact_ids)
                for artifact_id in record_artifact_ids:
                    artifact = record_artifacts.get(artifact_id)
                    artifact_path = (
                        Path(artifact.path) if artifact is not None else None
                    )
                    if (
                        artifact is None
                        or artifact_path is None
                        or not artifact_path.is_file()
                        or artifact_path.stat().st_size == 0
                    ):
                        raise ValueError(
                            f"partial fact bundle human recording {record_id} artifact must "
                            f"be a readable file: {artifact_id}"
                        )
    demos = value.get("demo_reproductions", [])
    if not isinstance(demos, list):
        raise ValueError("partial fact bundle demo_reproductions must be a list")
    try:
        validated_demos = [DemoReproduction.model_validate(item) for item in demos]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"partial fact bundle demo reproduction is invalid: {exc}"
        ) from exc
    screen_ids = {
        str(item.get("id"))
        for item in value.get("screen_states", [])
        if isinstance(item, dict) and item.get("id")
    }
    interaction_ids = {
        str(item.get("id"))
        for item in value.get("interactions", [])
        if isinstance(item, dict) and item.get("id")
    }
    interactions = value.get("interactions", [])
    if not isinstance(interactions, list):
        raise ValueError("partial fact bundle interactions must be a list")
    interaction_artifact_ids: list[str] = []
    for interaction in interactions:
        if not isinstance(interaction, dict):
            raise ValueError("partial fact bundle interaction must be an object")
        interaction_id = str(interaction.get("id") or "").strip()
        artifact_ids = interaction.get("artifact_ids", [])
        if not artifact_ids:
            continue
        if not isinstance(artifact_ids, list) or len(artifact_ids) < 2:
            raise ValueError(
                f"partial fact bundle interaction {interaction_id} requires before and "
                "after screenshot artifacts"
            )
        interaction_artifact_ids.extend(str(value) for value in artifact_ids[:2])
    interaction_artifacts = (
        facility.store.get_artifacts(interaction_artifact_ids)
        if facility is not None
        else {}
    )
    for interaction in interactions:
        interaction_id = str(interaction.get("id") or "").strip()
        artifact_ids = interaction.get("artifact_ids", [])
        if not artifact_ids or facility is None:
            continue
        for position, artifact_id in zip(
            ("before", "after"), artifact_ids[:2], strict=True
        ):
            artifact = interaction_artifacts.get(str(artifact_id))
            artifact_path = Path(artifact.path) if artifact is not None else None
            if (
                artifact is None
                or artifact.kind not in {"screenshot", "video_frame"}
                or artifact_path is None
                or not artifact_path.is_file()
                or artifact_path.stat().st_size == 0
            ):
                raise ValueError(
                    f"partial fact bundle interaction {interaction_id} {position} artifact "
                    f"must be a readable screenshot: {artifact_id}"
                )
    for demo in validated_demos:
        missing_screens = sorted(set(demo.covered_surface_ids) - screen_ids)
        missing_interactions = sorted(
            set(demo.covered_interaction_ids) - interaction_ids
        )
        if missing_screens:
            raise ValueError(
                f"partial fact bundle demo {demo.id} references unknown screens: {missing_screens}"
            )
        if missing_interactions:
            raise ValueError(
                f"partial fact bundle demo {demo.id} references unknown interactions: "
                f"{missing_interactions}"
            )
    value["demo_reproductions"] = [
        item.model_dump(mode="json") for item in validated_demos
    ]
    if _cache is not None:
        _cache[cache_key] = value
    return value


def _partial_fact_play_record_refs(
    facility: GameObservatory,
    bundle: dict[str, Any],
    *,
    draft_path: str = "",
) -> dict[str, dict[str, Any]]:
    source_ref_by_id = {
        str(item.get("id")): item
        for item in bundle.get("source_refs", [])
        if isinstance(item, dict) and item.get("id")
    }
    records = [
        record
        for record in bundle.get("play_records", [])
        if isinstance(record, dict) and record.get("id")
    ]
    artifact_by_id = facility.store.get_artifacts(
        str(artifact_id)
        for record in records
        for artifact_id in record.get("artifact_ids", [])
    )
    evidence_run_ids = [
        str(record.get("evidence_run_id") or "").strip()
        for record in records
        if str(record.get("evidence_run_id") or "").strip()
    ]
    run_by_id = facility.store.get_evidence_runs(evidence_run_ids)
    step_count_by_run_id = facility.store.count_evidence_steps_by_run_ids(
        evidence_run_ids
    )
    refs: dict[str, dict[str, Any]] = {}
    for record in records:
        sources = [
            source_ref_by_id[source_id]
            for source_id in record.get("source_ids", [])
            if source_id in source_ref_by_id
        ]
        artifacts: list[dict[str, Any]] = []
        for artifact_index, artifact_id in enumerate(
            record.get("artifact_ids", []), start=1
        ):
            artifact = artifact_by_id.get(str(artifact_id))
            if artifact is None:
                continue
            artifact_path = Path(artifact.path)
            metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
            artifacts.append(
                {
                    "id": artifact.id,
                    "title": str(
                        metadata.get("title")
                        or metadata.get("label")
                        or artifact.locator
                        or f"{record.get('title') or '游玩记录'} · 关键帧 {artifact_index}"
                    ),
                    "kind": artifact.kind,
                    "media_type": artifact.media_type,
                    "locator": artifact.locator,
                    "file_name": artifact_path.name,
                    "bytes": artifact_path.stat().st_size
                    if artifact_path.is_file()
                    else 0,
                    "href": (
                        "/api/game-observatory/internal/artifacts/"
                        f"{quote(artifact.id, safe='')}"
                    ),
                }
            )
        run_ref: dict[str, Any] | None = None
        evidence_run_id = str(record.get("evidence_run_id") or "").strip()
        if evidence_run_id:
            run = run_by_id.get(evidence_run_id)
            if run is not None:
                step_count = step_count_by_run_id.get(run.id, 0)
                run_ref = {
                    "id": run.id,
                    "status": run.status,
                    "adapter": run.adapter,
                    "target_id": run.target_id,
                    "build_scope_id": run.build_scope_id,
                    "scope_id": run.scope_id,
                    "started_at": run.started_at,
                    "ended_at": run.ended_at,
                    "step_count": step_count,
                    "artifact_count": len(run.artifact_ids),
                    "action_run_count": len(run.action_run_ids),
                    "observation_run_count": len(run.observation_run_ids),
                    "href": (
                        "/game-observatory/studio/evidence?"
                        + (f"draft={quote(draft_path, safe='')}&" if draft_path else "")
                        + f"run={quote(run.id, safe='')}"
                    ),
                    "sources_href": (
                        f"/game-observatory/studio/sources?run={quote(run.id, safe='')}"
                    ),
                }
                sources.append(
                    {
                        "id": f"source.evidence-run.{run.id}",
                        "kind": "direct_observation",
                        "title": f"{record.get('title') or '游玩记录'} · 实机运行证据",
                        "url": run_ref["href"],
                        "locator": (
                            f"{step_count} 个步骤 · {len(run.artifact_ids)} 个证据文件"
                        ),
                        "author": str(record.get("operator") or run.adapter),
                        "platform": str(record.get("platform") or run.adapter),
                        "published_at": str(
                            record.get("captured_on") or run.started_at
                        ),
                        "note": (
                            f"status={run.status}; target={run.target_id}; "
                            f"scope={run.scope_id or run.build_scope_id or 'unscoped'}"
                        ),
                    }
                )
        refs[str(record["id"])] = {
            "sources": sources,
            "artifacts": artifacts,
            "run": run_ref,
        }
    return refs


def _partial_fact_evidence_step_ids(value: Any) -> set[str]:
    step_ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_step_ids" and isinstance(item, list):
                step_ids.update(
                    str(step_id) for step_id in item if str(step_id).strip()
                )
            else:
                step_ids.update(_partial_fact_evidence_step_ids(item))
    elif isinstance(value, list):
        for item in value:
            step_ids.update(_partial_fact_evidence_step_ids(item))
    return step_ids


def _partial_fact_evidence_step_refs(
    facility: GameObservatory,
    bundle: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    step_ids = sorted(_partial_fact_evidence_step_ids(bundle))
    step_by_id = facility.store.get_evidence_steps(step_ids)
    for step_id in step_ids:
        step = step_by_id.get(step_id)
        if step is None:
            continue
        refs[step_id] = {
            "evidence_run_id": step.evidence_run_id,
            "step_index": step.step_index,
            "status": step.status,
        }
    return refs


def _partial_fact_projection_integrity(bundle: dict[str, Any]) -> dict[str, Any]:
    """Report broken references inside one already-materialized play projection.

    Derived views intentionally select only a slice of the shared fact graph.  A
    selected interaction is not operable when its source/terminal state or target
    UI element was filtered out, even though the underlying base bundle still has
    that object.  Keep this check cheap and deterministic so catalog/workspace
    callers can surface the problem without reopening the evidence store.
    """

    section_items = {
        key: [item for item in bundle.get(key, []) if isinstance(item, dict)]
        for key in (
            "screen_states",
            "ui_elements",
            "interactions",
            "state_transitions",
            "visible_mechanics",
            "resource_displays",
        )
    }
    ids = {
        key: {str(item.get("id")) for item in items if str(item.get("id") or "")}
        for key, items in section_items.items()
    }
    ui_by_id = {
        str(item.get("id")): item
        for item in section_items["ui_elements"]
        if str(item.get("id") or "")
    }
    interaction_by_id = {
        str(item.get("id")): item
        for item in section_items["interactions"]
        if str(item.get("id") or "")
    }
    issues: list[dict[str, str]] = []

    def add_issue(
        code: str,
        section: str,
        subject_id: str,
        reference_id: str,
        detail: str,
    ) -> None:
        issues.append(
            {
                "code": code,
                "section": section,
                "subject_id": subject_id,
                "reference_id": reference_id,
                "detail": detail,
            }
        )

    # Implicit closure remains a compatibility path for old drafts, but it is
    # not a stable publication boundary: adding an adjacent control or resource
    # to a shared screen would silently expand every derived play that omitted
    # that selector.  Surface the omission as integrity debt so canonical play
    # views can freeze their semantic slice without duplicating the facts.
    if bundle.get("base_bundle_path"):
        partition = (
            bundle.get("content_partition")
            if isinstance(bundle.get("content_partition"), dict)
            else {}
        )
        for selector_key in (
            "strict_surface_ids",
            "strict_ui_element_ids",
            "strict_interaction_ids",
            "strict_transition_ids",
            "strict_mechanic_ids",
            "strict_resource_ids",
            "strict_play_record_ids",
            "strict_evidence_gap_ids",
        ):
            if selector_key not in partition:
                add_issue(
                    "projection_selector_missing",
                    "content_partition",
                    str(bundle.get("id") or "derived-play-view"),
                    selector_key,
                    "Derived play view relies on implicit closure for this section.",
                )

    screen_ids = ids["screen_states"]
    if bundle.get("base_bundle_path"):
        family_state_ids = {
            str(state_id)
            for family in bundle.get("screen_families", [])
            if isinstance(family, dict)
            for state_id in family.get("screen_state_ids", [])
            if str(state_id).strip()
        }
        for state_id in sorted(screen_ids - family_state_ids):
            add_issue(
                "projection_surface_family_missing",
                "screen_states",
                state_id,
                "screen_families",
                "Selected screen state is not assigned to any projected screen family.",
            )

    for ui in section_items["ui_elements"]:
        ui_id = str(ui.get("id") or "")
        for state_id in [
            str(value) for value in ui.get("screen_state_ids", []) if str(value)
        ]:
            if state_id not in screen_ids:
                add_issue(
                    "ui_state_missing",
                    "ui_elements",
                    ui_id,
                    state_id,
                    "UI element references a screen state outside this projection.",
                )

    for interaction in section_items["interactions"]:
        interaction_id = str(interaction.get("id") or "")
        from_state_id = str(interaction.get("from_state_id") or "")
        to_state_id = str(interaction.get("to_state_id") or "")
        for field, state_id in (
            ("from_state_id", from_state_id),
            ("to_state_id", to_state_id),
        ):
            if state_id and state_id not in screen_ids:
                add_issue(
                    "interaction_state_missing",
                    "interactions",
                    interaction_id,
                    state_id,
                    f"Interaction {field} is outside this projection.",
                )
        input_value = interaction.get("input")
        target_id = (
            str(input_value.get("target") or "")
            if isinstance(input_value, dict)
            else ""
        )
        if not target_id:
            continue
        target = ui_by_id.get(target_id)
        if target is None:
            add_issue(
                "interaction_target_missing",
                "interactions",
                interaction_id,
                target_id,
                "Interaction target UI is absent from this projection.",
            )
            continue
        target_state_ids = {
            str(value) for value in target.get("screen_state_ids", []) if str(value)
        }
        if from_state_id and target_state_ids and from_state_id not in target_state_ids:
            add_issue(
                "interaction_target_wrong_state",
                "interactions",
                interaction_id,
                target_id,
                "Interaction target UI is not declared on the source screen state.",
            )

    for transition in section_items["state_transitions"]:
        transition_id = str(transition.get("id") or "")
        from_state_id = str(transition.get("from_state_id") or "")
        to_state_id = str(transition.get("to_state_id") or "")
        for field, state_id in (
            ("from_state_id", from_state_id),
            ("to_state_id", to_state_id),
        ):
            if state_id and state_id not in screen_ids:
                add_issue(
                    "transition_state_missing",
                    "state_transitions",
                    transition_id,
                    state_id,
                    f"Transition {field} is outside this projection.",
                )
        for interaction_id in [
            str(value)
            for value in transition.get("via_interaction_ids", [])
            if str(value)
        ]:
            interaction = interaction_by_id.get(interaction_id)
            if interaction is None:
                add_issue(
                    "transition_interaction_missing",
                    "state_transitions",
                    transition_id,
                    interaction_id,
                    "Transition references an interaction outside this projection.",
                )
                continue
            if (
                str(interaction.get("from_state_id") or "") != from_state_id
                or str(interaction.get("to_state_id") or "") != to_state_id
            ):
                add_issue(
                    "transition_interaction_endpoint_mismatch",
                    "state_transitions",
                    transition_id,
                    interaction_id,
                    "Transition and interaction do not share the same endpoints.",
                )

    for mechanic in section_items["visible_mechanics"]:
        mechanic_id = str(mechanic.get("id") or "")
        for state_id in [
            str(value) for value in mechanic.get("screen_state_ids", []) if str(value)
        ]:
            if state_id not in screen_ids:
                add_issue(
                    "mechanic_state_missing",
                    "visible_mechanics",
                    mechanic_id,
                    state_id,
                    "Visible mechanic references a screen state outside this projection.",
                )

    for resource in section_items["resource_displays"]:
        resource_id = str(resource.get("id") or "")
        state_id = str(resource.get("screen_state_id") or "")
        if state_id and state_id not in screen_ids:
            add_issue(
                "resource_state_missing",
                "resource_displays",
                resource_id,
                state_id,
                "Resource display references a screen state outside this projection.",
            )

    issues.sort(
        key=lambda item: (
            item["section"],
            item["subject_id"],
            item["code"],
            item["reference_id"],
        )
    )
    return {"issue_count": len(issues), "issues": issues}


def _partial_fact_bundle_summary(path: str, bundle: dict[str, Any]) -> dict[str, Any]:
    scope = bundle.get("scope") if isinstance(bundle.get("scope"), dict) else {}
    game = bundle.get("game") if isinstance(bundle.get("game"), dict) else {}
    play = bundle.get("play") if isinstance(bundle.get("play"), dict) else {}
    section_keys = (
        "screen_states",
        "ui_elements",
        "interactions",
        "state_transitions",
        "visible_mechanics",
        "resource_displays",
        "evidence_gaps",
    )
    screens = (
        bundle.get("screen_states")
        if isinstance(bundle.get("screen_states"), list)
        else []
    )
    first_screen = screens[0] if screens and isinstance(screens[0], dict) else {}
    first_artifacts = (
        first_screen.get("artifact_ids")
        if isinstance(first_screen.get("artifact_ids"), list)
        else []
    )
    primary_evidence_run_id = str(bundle.get("evidence_run_id") or "")
    evidence_run_ids = [
        str(value) for value in bundle.get("evidence_run_ids", []) if str(value).strip()
    ]
    if primary_evidence_run_id:
        evidence_run_ids.insert(0, primary_evidence_run_id)
    partition = (
        bundle.get("content_partition")
        if isinstance(bundle.get("content_partition"), dict)
        else {}
    )
    strict_surface_ids = {
        str(value) for value in partition.get("strict_surface_ids", []) if str(value)
    }
    strict_mechanic_ids = {
        str(value) for value in partition.get("strict_mechanic_ids", []) if str(value)
    }
    strict_counts = {
        "screen_states": len(
            [
                item
                for item in bundle.get("screen_states", [])
                if not strict_surface_ids or str(item.get("id")) in strict_surface_ids
            ]
        ),
        "ui_elements": len(
            [
                item
                for item in bundle.get("ui_elements", [])
                if not strict_surface_ids
                or set(item.get("screen_state_ids") or []) & strict_surface_ids
            ]
        ),
        "interactions": len(
            [
                item
                for item in bundle.get("interactions", [])
                if not strict_surface_ids
                or {
                    str(item.get("from_state_id")),
                    str(item.get("to_state_id")),
                }
                <= strict_surface_ids
            ]
        ),
        "state_transitions": len(
            [
                item
                for item in bundle.get("state_transitions", [])
                if not strict_surface_ids
                or {
                    str(item.get("from_state_id")),
                    str(item.get("to_state_id")),
                }
                <= strict_surface_ids
            ]
        ),
        "visible_mechanics": len(
            [
                item
                for item in bundle.get("visible_mechanics", [])
                if not strict_mechanic_ids or str(item.get("id")) in strict_mechanic_ids
            ]
        ),
        "resource_displays": len(
            [
                item
                for item in bundle.get("resource_displays", [])
                if not strict_surface_ids
                or str(item.get("screen_state_id")) in strict_surface_ids
            ]
        ),
        "evidence_gaps": len(bundle.get("evidence_gaps", [])),
    }
    play_tags = list(play.get("tags") or [])
    game_id = str(game.get("id") or scope.get("game_id") or "")
    play_slug = str(play.get("slug") or bundle.get("id") or path)
    content_kind = str(bundle.get("content_kind") or "play")
    reader_position = reader_content_position(
        game_id,
        play_slug,
        content_kind=content_kind,
    )
    reader = build_reader_projection(bundle, position=reader_position)
    reader_game = build_reader_game_projection(bundle)
    return {
        "path": path,
        "storage_kind": (
            "derived-play-view"
            if bundle.get("base_bundle_path")
            else "standalone-bundle"
        ),
        "base_bundle_path": str(bundle.get("base_bundle_path") or ""),
        "base_bundle_id": str(bundle.get("base_bundle_id") or ""),
        "id": str(bundle.get("id") or path),
        "status": str(bundle.get("status") or "partial"),
        "publication_ready": bundle.get("publication_ready") is True,
        "reader_visibility": str(reader_position.get("reader_visibility") or "public"),
        "content_kind": content_kind,
        "reader_position": reader_position,
        "coverage_claim": str(bundle.get("coverage_claim") or "play-boundary"),
        "boundary_manifest": str(bundle.get("boundary_manifest") or ""),
        "evidence_run_id": primary_evidence_run_id,
        "evidence_run_ids": list(dict.fromkeys(evidence_run_ids)),
        "game_id": game_id,
        "game_slug": str(
            game.get("slug") or game.get("id") or scope.get("game_id") or ""
        ),
        "game_title": str(game.get("title") or scope.get("game_id") or ""),
        "game_localized_title": str(game.get("localized_title") or ""),
        "reader_game": reader_game,
        "game_tags": list(game.get("tags") or []),
        "play_id": str(play.get("id") or bundle.get("id") or path),
        "play_slug": play_slug,
        "play_title": str(
            play.get("title") or scope.get("subject") or bundle.get("id") or path
        ),
        "reader": reader,
        "play_tags": play_tags,
        "play_tag_details": public_play_tag_details(play_tags),
        "cover_artifact_id": str(first_artifacts[0]) if first_artifacts else "",
        "subject": str(scope.get("subject") or bundle.get("id") or path),
        "coverage": str(scope.get("coverage") or ""),
        "counts": {key: strict_counts.get(key, 0) for key in section_keys},
        "projection_integrity": _partial_fact_projection_integrity(bundle),
    }


def _partial_fact_duplicate_issues(
    summaries: list[dict[str, Any]],
) -> dict[str, str]:
    """Return every ambiguous bundle path instead of choosing one by scan order."""

    identity_groups: dict[tuple[str, ...], list[str]] = {}
    identity_labels: dict[tuple[str, ...], str] = {}
    summaries_by_path: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        path = str(summary.get("path") or "")
        if not path:
            continue
        summaries_by_path[path] = summary
        identities = (
            (
                ("bundle-id", str(summary.get("id") or "")),
                f"bundle id {summary.get('id')}",
            ),
            (
                (
                    "play-id",
                    str(summary.get("game_id") or ""),
                    str(summary.get("play_id") or ""),
                ),
                f"game/play id {summary.get('game_id')}/{summary.get('play_id')}",
            ),
            (
                (
                    "play-slug",
                    str(summary.get("game_slug") or ""),
                    str(summary.get("play_slug") or ""),
                ),
                f"game/play slug {summary.get('game_slug')}/{summary.get('play_slug')}",
            ),
        )
        for identity, label in identities:
            if any(not part for part in identity[1:]):
                continue
            identity_groups.setdefault(identity, []).append(path)
            identity_labels[identity] = label

    issues: dict[str, list[str]] = {}
    for identity, paths in identity_groups.items():
        unique_paths = list(dict.fromkeys(paths))
        if len(unique_paths) < 2:
            continue
        if identity[0] != "bundle-id":
            published_paths = [
                path
                for path in unique_paths
                if summaries_by_path[path].get("publication_ready") is True
            ]
            if len(published_paths) == 1:
                canonical_path = published_paths[0]
                detail = (
                    f"{identity_labels[identity]} is shadowed by published canonical "
                    f"{canonical_path}"
                )
                for path in unique_paths:
                    if path != canonical_path:
                        issues.setdefault(path, []).append(detail)
                continue
        conflict = ", ".join(sorted(unique_paths))
        detail = f"{identity_labels[identity]} conflicts across {conflict}"
        for path in unique_paths:
            issues.setdefault(path, []).append(detail)
    return {
        path: "duplicate partial fact identity: " + "; ".join(sorted(details))
        for path, details in issues.items()
    }


def _partial_fact_workspace_entries(
    facility: GameObservatory,
    *,
    publication_ready_only: bool = False,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, str]], int]:
    """Load the draft workspace once with shared validation and identity handling."""

    root = _partial_fact_drafts_root(facility)
    entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[dict[str, str]] = []
    ignored = 0
    load_cache: dict[Path, dict[str, Any]] = {}
    if root.is_dir():
        with facility.store.read_session():
            for candidate in sorted(root.rglob("*.json")):
                try:
                    resolved = candidate.resolve()
                    relative_path = resolved.relative_to(root).as_posix()
                    value = json.loads(resolved.read_text(encoding="utf-8"))
                    if (
                        not isinstance(value, dict)
                        or value.get("schema") != _PARTIAL_FACT_BUNDLE_SCHEMA
                    ):
                        ignored += 1
                        continue
                    bundle = _load_partial_fact_bundle(
                        resolved, facility, _cache=load_cache
                    )
                    entries.append(
                        (_partial_fact_bundle_summary(relative_path, bundle), bundle)
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    try:
                        display_path = candidate.relative_to(root).as_posix()
                    except ValueError:
                        display_path = candidate.name
                    errors.append({"path": display_path, "detail": str(exc)})

    if publication_ready_only:
        entries = [
            (summary, bundle)
            for summary, bundle in entries
            if bundle.get("publication_ready") is True
            and summary.get("reader_visibility") == "public"
        ]
    duplicate_issues = _partial_fact_duplicate_issues(
        [summary for summary, _bundle in entries]
    )
    if duplicate_issues:
        entries = [
            (summary, bundle)
            for summary, bundle in entries
            if summary["path"] not in duplicate_issues
        ]
        errors.extend(
            {"path": path, "detail": detail}
            for path, detail in sorted(duplicate_issues.items())
        )
    return entries, errors, ignored


def _published_partial_fact_entries(
    facility: GameObservatory,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    entries, _errors, _ignored = _partial_fact_workspace_entries(
        facility, publication_ready_only=True
    )
    return entries


def _published_partial_fact_image_artifact_ids(facility: GameObservatory) -> set[str]:
    """Return image artifacts referenced by reader-visible published bundles."""

    candidates: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "artifact_id" and child:
                    candidates.add(str(child))
                elif key == "artifact_ids" and isinstance(child, list):
                    candidates.update(str(item) for item in child if item)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for summary, bundle in _published_partial_fact_entries(facility):
        if summary.get("cover_artifact_id"):
            candidates.add(str(summary["cover_artifact_id"]))
        collect(bundle)
    artifacts = facility.store.get_artifacts(candidates)
    return {
        artifact_id
        for artifact_id, artifact in artifacts.items()
        if str(artifact.media_type or "").startswith("image/")
    }


def _public_partial_fact_catalog_item(
    summary: dict[str, Any], bundle: dict[str, Any]
) -> dict[str, Any]:
    build = bundle.get("build") if isinstance(bundle.get("build"), dict) else {}
    partition = (
        bundle.get("content_partition")
        if isinstance(bundle.get("content_partition"), dict)
        else {}
    )
    strict_surface_ids = {
        str(value) for value in partition.get("strict_surface_ids", []) if str(value)
    }
    screen_states = [
        item
        for item in (
            bundle.get("screen_states")
            if isinstance(bundle.get("screen_states"), list)
            else []
        )
        if not strict_surface_ids or str(item.get("id")) in strict_surface_ids
    ]
    feedback = (
        [
            item
            for item in bundle.get("community_feedback", [])
            if item.get("content_scope") != "game"
        ]
        if isinstance(bundle.get("community_feedback"), list)
        else []
    )
    game_slug = quote(summary["game_slug"], safe="")
    play_slug = quote(summary["play_slug"], safe="")
    cover_artifact_id = summary.get("cover_artifact_id") or ""
    return {
        "schema": "game-observatory.public-play-summary.v1",
        "id": summary["id"],
        "slug": summary["play_slug"],
        "status": summary["status"],
        "publication_ready": summary["publication_ready"],
        "content_kind": summary["content_kind"],
        "coverage_claim": summary["coverage_claim"],
        "game_id": summary["game_id"],
        "game_title": summary["game_title"],
        "game": {
            "slug": summary["game_slug"],
            "localized_title": summary["reader_game"]["title"],
            "summary": summary["reader_game"]["summary"],
            "tags": summary["game_tags"],
        },
        "play": {
            "id": summary["play_id"],
            "slug": summary["play_slug"],
            "title": summary["reader"]["title"],
            "tags": summary["play_tags"],
            "tag_details": summary["play_tag_details"],
        },
        "system_title": summary["reader"]["title"],
        "summary": summary["reader"]["summary"],
        "reader": summary["reader"],
        "tags": list(dict.fromkeys([*summary["play_tags"], *summary["game_tags"]])),
        "scope": {
            "version": str(build.get("build_scope_id") or "已观测版本"),
            "game_id": summary["game_id"],
        },
        "surfaces": [
            {"id": item.get("id", "")}
            for item in screen_states
            if isinstance(item, dict)
        ],
        "artifacts": (
            [
                {
                    "id": cover_artifact_id,
                    "url": (
                        "/api/game-observatory/artifacts/"
                        f"{quote(cover_artifact_id, safe='')}"
                    ),
                }
            ]
            if cover_artifact_id
            else []
        ),
        "cover_artifact_id": cover_artifact_id,
        "player_voices": feedback,
        "compiled": {"object_index": []},
        "contract_version": _PARTIAL_FACT_BUNDLE_SCHEMA,
        "public_path": (
            f"/game-observatory/studio/play?game={game_slug}&play={play_slug}&published=1"
        ),
    }


def _partial_fact_search_results(
    entries: list[tuple[dict[str, Any], dict[str, Any]]],
    query: str,
    *,
    facility: GameObservatory,
    limit: int,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    term = query.strip().casefold()
    results: dict[str, dict[str, Any]] = {}

    def add(
        result_id: str,
        result_type: str,
        title: Any,
        copy: Any,
        tags: Any,
        href: str,
        raw: Any,
        summary: dict[str, Any],
    ) -> None:
        searchable = (
            title,
            copy,
            tags,
            raw,
            summary["game_title"],
            summary["play_title"],
        )
        if (
            term
            and term
            not in json.dumps(searchable, ensure_ascii=False, default=str).casefold()
        ):
            return
        if not term and not include_all and result_type not in {"游戏", "玩法"}:
            return
        scoped_result_id = (
            result_id
            if result_type in {"游戏", "玩法"}
            else f"{summary['play_id']}:{result_id}"
        )
        raw_object = raw if isinstance(raw, dict) else {}
        source_object = (
            raw_object.get("source")
            if isinstance(raw_object.get("source"), dict)
            else {}
        )
        record_object = (
            raw_object.get("record")
            if isinstance(raw_object.get("record"), dict)
            else raw_object
        )
        raw_artifact_ids = (
            raw_object.get("artifact_ids")
            if isinstance(raw_object.get("artifact_ids"), list)
            else []
        )
        resolved_artifacts = (
            raw_object.get("artifacts")
            if isinstance(raw_object.get("artifacts"), list)
            else []
        )
        image_artifact_id = str(
            raw_object.get("preview_artifact_id")
            or next(
                (value for value in raw_artifact_ids if str(value).startswith("art.")),
                "",
            )
            or next(
                (
                    artifact.get("id")
                    for artifact in resolved_artifacts
                    if isinstance(artifact, dict) and artifact.get("id")
                ),
                "",
            )
            or (
                summary.get("cover_artifact_id")
                if result_type in {"游戏", "玩法", "玩法设计文档"}
                else ""
            )
        )
        results[scoped_result_id] = {
            "id": scoped_result_id,
            "type": result_type,
            "title": str(title or ""),
            "copy": str(copy or ""),
            "tags": [str(value) for value in tags or []],
            "href": href,
            "game_id": summary["game_id"],
            "game_title": summary["game_title"],
            "play_id": summary["play_id"],
            "play_title": summary.get("reader", {}).get("title")
            or summary["play_title"],
            "platform": str(
                source_object.get("platform") or record_object.get("platform") or ""
            ),
            "content_scope": str(
                raw_object.get("content_scope")
                or ("game" if result_type == "游戏" else "play")
            ),
            "image_artifact_id": image_artifact_id,
        }

    for summary, bundle in entries:
        partition = (
            bundle.get("content_partition")
            if isinstance(bundle.get("content_partition"), dict)
            else {}
        )
        strict_surface_ids = {
            str(value)
            for value in partition.get("strict_surface_ids", [])
            if str(value)
        }
        strict_mechanic_ids = {
            str(value)
            for value in partition.get("strict_mechanic_ids", [])
            if str(value)
        }
        strict_play_record_ids = {
            str(value)
            for value in partition.get("strict_play_record_ids", [])
            if str(value)
        }
        play_record_refs = _partial_fact_play_record_refs(facility, bundle)
        game_slug = quote(summary["game_slug"], safe="")
        play_slug = quote(summary["play_slug"], safe="")
        draft = quote(summary["path"], safe="")
        play_url = f"/game-observatory/studio/play?game={game_slug}&play={play_slug}"
        coverage_url = f"/game-observatory/studio/coverage?draft={draft}"
        screens = {
            str(item.get("id")): item
            for item in bundle.get("screen_states", [])
            if (
                isinstance(item, dict)
                and item.get("id")
                and (
                    not strict_surface_ids or str(item.get("id")) in strict_surface_ids
                )
            )
        }
        screen_tags = {
            str(item.get("screen_state_id")): list(item.get("tags") or [])
            for item in bundle.get("screen_tags", [])
            if (
                isinstance(item, dict)
                and item.get("screen_state_id")
                and str(item.get("screen_state_id")) in screens
            )
        }
        add(
            f"game:{summary['game_id']}",
            "游戏",
            summary["game_title"],
            summary["game_localized_title"],
            summary["game_tags"],
            f"/game-observatory/studio/game?game={game_slug}",
            bundle.get("game"),
            summary,
        )
        add(
            f"play:{summary['play_id']}",
            "玩法",
            summary["reader"]["title"],
            summary["reader"]["summary"],
            summary["play_tags"],
            play_url,
            bundle.get("play"),
            summary,
        )
        add(
            f"design-document:{summary['id']}",
            "玩法设计文档",
            summary["reader"]["title"],
            summary["reader"]["summary"],
            summary["play_tags"],
            f"{play_url}&section=design",
            bundle.get("scope"),
            summary,
        )
        for item in screens.values():
            item_id = str(item["id"])
            add(
                f"screen:{item_id}",
                "界面",
                item.get("name"),
                "；".join(str(value) for value in item.get("visible_facts") or []),
                screen_tags.get(item_id, []),
                f"{play_url}&section=interfaces#reader-{quote(item_id, safe='')}",
                item,
                summary,
            )
        for item in bundle.get("ui_elements", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if (
                strict_surface_ids
                and not set(item.get("screen_state_ids") or []) & strict_surface_ids
            ):
                continue
            item_id = str(item["id"])
            screen_names = [
                str(screens.get(str(screen_id), {}).get("name") or screen_id)
                for screen_id in item.get("screen_state_ids") or []
            ]
            add(
                f"element:{item_id}",
                "界面元素",
                item.get("name"),
                " · ".join([str(item.get("role") or ""), *screen_names]).strip(" ·"),
                [],
                f"{play_url}&section=interfaces",
                item,
                summary,
            )
        for item in bundle.get("interactions", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if (
                strict_surface_ids
                and not {
                    str(item.get("from_state_id")),
                    str(item.get("to_state_id")),
                }
                <= strict_surface_ids
            ):
                continue
            item_id = str(item["id"])
            add(
                f"interaction:{item_id}",
                "交互",
                item.get("immediate_feedback") or item_id,
                json.dumps(item.get("input") or {}, ensure_ascii=False),
                [],
                f"{play_url}&section=interfaces#reader-{quote(item_id, safe='')}",
                item,
                summary,
            )
        for item in bundle.get("state_transitions", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if (
                strict_surface_ids
                and not {
                    str(item.get("from_state_id")),
                    str(item.get("to_state_id")),
                }
                <= strict_surface_ids
            ):
                continue
            item_id = str(item["id"])
            source_name = screens.get(str(item.get("from_state_id")), {}).get(
                "name"
            ) or item.get("from_state_id")
            target_name = screens.get(str(item.get("to_state_id")), {}).get(
                "name"
            ) or item.get("to_state_id")
            add(
                f"transition:{item_id}",
                "状态转换",
                f"{source_name} → {target_name}",
                " · ".join(
                    str(value) for value in item.get("via_interaction_ids") or []
                ),
                [],
                f"{play_url}&section=interfaces#reader-{quote(item_id, safe='')}",
                item,
                summary,
            )
        for item in bundle.get("visible_mechanics", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if strict_mechanic_ids and str(item.get("id")) not in strict_mechanic_ids:
                continue
            item_id = str(item["id"])
            add(
                f"mechanic:{item_id}",
                "机制",
                item.get("observed_rule"),
                "",
                [],
                f"{play_url}&section=design",
                item,
                summary,
            )
        for item in bundle.get("resource_displays", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if (
                strict_surface_ids
                and str(item.get("screen_state_id")) not in strict_surface_ids
            ):
                continue
            item_id = str(item["id"])
            value = item.get("displayed_value")
            copy = (
                " · ".join(str(part) for part in value)
                if isinstance(value, list)
                else value
            )
            add(
                f"resource:{item_id}",
                "资源",
                item.get("label"),
                copy,
                [],
                f"{play_url}&section=design",
                item,
                summary,
            )
        for item in bundle.get("evidence_gaps", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            item_id = str(item["id"])
            add(
                f"gap:{item_id}",
                "待确认内容",
                item.get("subject"),
                f"{item.get('reason') or ''} {item.get('required_evidence') or ''}".strip(),
                [],
                f"{coverage_url}#{quote(item_id, safe='')}",
                item,
                summary,
            )
        for screen_id, tags in screen_tags.items():
            add(
                f"screen-tags:{screen_id}",
                "界面 tag",
                screens.get(screen_id, {}).get("name") or screen_id,
                " · ".join(tags),
                tags,
                f"{play_url}&section=screen-tags",
                {"screen_state_id": screen_id, "tags": tags},
                summary,
            )
        for item in bundle.get("play_records", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if (
                strict_play_record_ids
                and str(item.get("id")) not in strict_play_record_ids
            ):
                continue
            resolved_refs = play_record_refs.get(str(item["id"]), {})
            sources = resolved_refs.get("sources", [])
            artifacts = resolved_refs.get("artifacts", [])
            origin_labels = [
                str(source.get("title") or source.get("platform") or "")
                for source in sources
            ] + [
                str(artifact.get("title") or artifact.get("file_name") or "")
                for artifact in artifacts
            ]
            add(
                f"play-record:{item['id']}",
                "游玩记录",
                item.get("title"),
                " · ".join(
                    value
                    for value in [
                        str(item.get("source_type") or ""),
                        str(item.get("platform") or ""),
                        *origin_labels,
                    ]
                    if value
                ),
                [],
                f"{play_url}&section=records",
                {"record": item, "sources": sources, "artifacts": artifacts},
                summary,
            )
        for item in bundle.get("community_feedback", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            add(
                f"feedback:{item['id']}",
                "反馈",
                item.get("title") or item.get("summary"),
                f"{item.get('source_type') or ''} {source.get('platform') or ''} {source.get('author') or source.get('account') or ''}".strip(),
                item.get("tags") or [],
                (
                    f"/game-observatory/studio/game?game={game_slug}#game-feedback"
                    if item.get("content_scope") == "game"
                    else f"{play_url}&section=feedback"
                ),
                item,
                summary,
            )
            if source.get("id") and source.get("url") and source.get("platform"):
                add(
                    f"source:{source['id']}",
                    "来源",
                    source.get("title") or source.get("url"),
                    " · ".join(
                        str(value)
                        for value in (
                            source.get("platform"),
                            source.get("author") or source.get("account"),
                            source.get("published_at"),
                        )
                        if value
                    ),
                    [source.get("source_type") or source.get("kind")],
                    (
                        f"/game-observatory/studio/game?game={game_slug}#game-feedback"
                        if item.get("content_scope") == "game"
                        else f"{play_url}&section=feedback"
                    ),
                    source,
                    summary,
                )
        for item in bundle.get("demo_reproductions", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if (
                strict_surface_ids
                and not set(item.get("covered_surface_ids") or []) <= strict_surface_ids
            ):
                continue
            add(
                f"demo:{item['id']}",
                "Demo",
                item.get("title"),
                item.get("description"),
                item.get("tags") or [],
                str(item.get("url") or f"{play_url}#play-demo"),
                item,
                summary,
            )
    return list(results.values())[:limit]


def _public_search_results(
    reports: list[GameReport], query: str
) -> list[dict[str, Any]]:
    term = query.strip().casefold()
    if not term:
        return []

    def matches(*values: Any) -> bool:
        return term in json.dumps(values, ensure_ascii=False, default=str).casefold()

    results: list[dict[str, Any]] = []
    seen_games: set[str] = set()
    for report in reports:
        play_url = f"/game-observatory/play/{report.slug}"
        game_slug = (
            report.game.slug if report.game and report.game.slug else report.game_id
        )
        play_title = report.play.title if report.play else report.system_title
        if report.game_id not in seen_games and matches(
            report.game_id,
            report.game_title,
            report.game.aliases if report.game else [],
            report.game.platforms if report.game else [],
        ):
            seen_games.add(report.game_id)
            results.append(
                {
                    "id": f"game:{report.game_id}",
                    "type": "game",
                    "title": report.game_title,
                    "summary": "游戏",
                    "game_id": report.game_id,
                    "game_title": report.game_title,
                    "play_slug": None,
                    "play_title": None,
                    "href": f"/game-observatory/game/{game_slug}",
                    "tags": [],
                }
            )
        if matches(play_title, report.play, report.summary, report.tags):
            results.append(
                {
                    "id": f"play:{report.id}",
                    "type": "play",
                    "title": play_title,
                    "summary": report.summary,
                    "game_id": report.game_id,
                    "game_title": report.game_title,
                    "play_slug": report.slug,
                    "play_title": play_title,
                    "href": play_url,
                    "tags": report.tags,
                }
            )
        for surface in report.surfaces:
            if matches(surface.title, surface.description, surface.kind):
                results.append(
                    {
                        "id": f"surface:{surface.id}",
                        "type": "interface",
                        "title": surface.title,
                        "summary": surface.description or "玩法界面",
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#{surface.id}",
                        "tags": [surface.kind],
                    }
                )
            for element in surface.elements:
                if matches(element.label, element.text, element.role, element.actions):
                    results.append(
                        {
                            "id": f"element:{element.id}",
                            "type": "interface_element",
                            "title": element.label or element.text or element.role,
                            "summary": f"{surface.title} · {element.role}",
                            "game_id": report.game_id,
                            "game_title": report.game_title,
                            "play_slug": report.slug,
                            "play_title": report.system_title,
                            "href": f"{play_url}#{element.id}",
                            "tags": element.actions,
                        }
                    )
        for node in report.flow:
            if matches(
                node.title,
                node.description,
                node.action,
                node.state_before,
                node.state_after,
            ):
                results.append(
                    {
                        "id": f"interaction:{node.id}",
                        "type": "interaction",
                        "title": node.title,
                        "summary": node.description,
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#{node.id}",
                        "tags": [],
                    }
                )
        for mechanism in report.mechanisms:
            if matches(mechanism.title, mechanism.description, mechanism.code):
                results.append(
                    {
                        "id": f"mechanism:{mechanism.id}",
                        "type": "mechanism",
                        "title": mechanism.title,
                        "summary": mechanism.description,
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#{mechanism.id}",
                        "tags": [mechanism.representation],
                    }
                )
        for relation in report.resources:
            if matches(relation.resource, relation.description, relation.role):
                results.append(
                    {
                        "id": f"resource:{relation.id}",
                        "type": "resource",
                        "title": relation.resource,
                        "summary": relation.description,
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#{relation.id}",
                        "tags": [relation.role],
                    }
                )
        for binding in report.screen_tags:
            surface = next(
                (item for item in report.surfaces if item.id == binding.surface_id),
                None,
            )
            if matches(binding.tags, surface.title if surface else binding.surface_id):
                results.append(
                    {
                        "id": f"screen-tags:{binding.surface_id}",
                        "type": "screen_tags",
                        "title": f"{surface.title if surface else binding.surface_id} · 界面 tag",
                        "summary": " · ".join(binding.tags),
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#play-screen-tags",
                        "tags": binding.tags,
                    }
                )
        for record in report.play_records:
            if matches(record.model_dump(mode="json")):
                results.append(
                    {
                        "id": f"play-record:{record.id}",
                        "type": "play_record",
                        "title": record.title,
                        "summary": f"{record.platform} · {record.captured_at}",
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#play-records",
                        "tags": [record.source_type],
                    }
                )
        for demo in report.demo_reproductions:
            if matches(demo.model_dump(mode="json")):
                results.append(
                    {
                        "id": f"demo:{demo.id}",
                        "type": "demo",
                        "title": demo.title,
                        "summary": demo.description,
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#play-demos",
                        "tags": demo.tags,
                    }
                )
        for item in report.community_feedback:
            if matches(item.model_dump(mode="json")):
                source = item.source
                results.append(
                    {
                        "id": f"community-feedback:{item.id}",
                        "type": "feedback",
                        "title": item.title,
                        "summary": item.summary,
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#spec-voices",
                        "tags": item.tags,
                        "source": {
                            "platform": source.platform,
                            "url": source.url,
                            "author": source.author,
                            "account": source.account,
                            "published_at": source.published_at,
                            "source_type": source.source_type,
                        },
                    }
                )
        source_by_id = {item.id: item for item in report.sources}
        for source in report.sources:
            if source.public and matches(source.model_dump(mode="json")):
                results.append(
                    {
                        "id": f"source:{source.id}",
                        "type": "source",
                        "title": source.title,
                        "summary": " · ".join(
                            item
                            for item in (
                                source.platform,
                                source.author or source.account,
                                source.published_at,
                            )
                            if item
                        ),
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#source-{source.id}",
                        "tags": [source.source_type]
                        if source.source_type
                        else [source.kind.value],
                        "source": {
                            "platform": source.platform,
                            "url": source.url,
                            "author": source.author,
                            "account": source.account,
                            "published_at": source.published_at,
                            "source_type": source.source_type,
                        },
                    }
                )
        for voice in report.player_voices:
            source = source_by_id.get(voice.source_id)
            if matches(
                voice.summary,
                voice.theme,
                voice.quote,
                voice.tags,
                source.title if source else "",
                source.platform if source else "",
                source.author if source else "",
            ):
                results.append(
                    {
                        "id": f"feedback:{voice.id}",
                        "type": "feedback",
                        "title": voice.summary,
                        "summary": (
                            f"{source.platform} · {source.title}"
                            if source and source.platform
                            else source.title
                            if source
                            else voice.theme
                        ),
                        "game_id": report.game_id,
                        "game_title": report.game_title,
                        "play_slug": report.slug,
                        "play_title": report.system_title,
                        "href": f"{play_url}#spec-voices",
                        "tags": voice.tags,
                        "source": (
                            {
                                "platform": source.platform,
                                "url": source.url,
                                "author": source.author,
                                "account": source.account,
                                "published_at": source.published_at,
                                "source_type": source.source_type,
                            }
                            if source and source.public
                            else None
                        ),
                    }
                )
        if report.design_spec and matches(report.design_spec.model_dump(mode="json")):
            results.append(
                {
                    "id": f"design-document:{report.design_spec.id}",
                    "type": "play_design_document",
                    "title": f"{report.design_spec.title} · 玩法设计文档",
                    "summary": report.summary,
                    "game_id": report.game_id,
                    "game_title": report.game_title,
                    "play_slug": report.slug,
                    "play_title": report.system_title,
                    "href": play_url,
                    "tags": report.tags,
                }
            )
    deduplicated = {item["id"]: item for item in results}
    return list(deduplicated.values())[:200]


def _set_public_cache(response: Response, value: Any, *, seconds: int = 60) -> None:
    response.headers["ETag"] = _etag(value)
    response.headers["Cache-Control"] = (
        f"public, max-age={seconds}, stale-while-revalidate={seconds * 5}"
    )


class CaptureBody(BaseModel):
    serial: str
    lease_token: str | None = None


class ActionBody(BaseModel):
    serial: str
    action: NormalizedAction
    lease_token: str | None = None


class SourceIngestBody(BaseModel):
    report_id: str
    source: SourceRef
    excerpt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceAcquireBody(SourceIngestBody):
    acquisition_url: str | None = None


class VoiceIngestBody(BaseModel):
    report_id: str
    source: SourceRef
    voice: PlayerVoice
    excerpt: str | None = None


class CommunityFeedbackIngestBody(BaseModel):
    report_id: str
    feedback: CommunityFeedbackItem
    excerpt: str | None = None
    acquisition_url: str | None = None


class VoiceReviewBody(BaseModel):
    decision: str = Field(pattern="^(reviewed|rejected)$")
    reviewer: str
    note: str | None = None


class RetractionBody(BaseModel):
    reason: str


class ReviewTransitionBody(BaseModel):
    status: str = Field(pattern="^(draft|review|published)$")
    note: str | None = None


class AfkLivePromotionBody(BaseModel):
    manifest_path: str


class MinecraftLivePromotionBody(BaseModel):
    manifest_path: str


class LeaseAcquireBody(BaseModel):
    target_id: str
    holder: str
    ttl_seconds: int = Field(default=300, ge=15, le=1800)


class LeaseTokenBody(BaseModel):
    token: str
    ttl_seconds: int = Field(default=300, ge=15, le=1800)


class TargetLeaseBody(BaseModel):
    target_id: str
    lease_token: str


class PackageOperationBody(TargetLeaseBody):
    package: str


class InstallApkBody(TargetLeaseBody):
    apk_path: str


class CaptureStreamBody(TargetLeaseBody):
    frame_count: int = Field(default=10, ge=1, le=600)
    interval_seconds: float = Field(default=0.25, ge=0.05, le=10.0)
    include_ui_every: int = Field(default=0, ge=0, le=600)
    max_recoveries: int = Field(default=2, ge=0, le=10)


class EvidenceRunStartBody(TargetLeaseBody):
    viewport_width: int = Field(gt=0, le=16_384)
    viewport_height: int = Field(gt=0, le=16_384)
    game_id: str | None = None
    build_scope_id: str | None = None
    scope_id: str | None = None
    environment: dict[str, Any] = Field(default_factory=dict)


class EvidenceStepBody(BaseModel):
    lease_token: str
    action: NormalizedAction
    target_name: str | None = None
    target_bounds: SourcePixelRect | None = None
    settle_threshold: float = Field(default=0.01, ge=0, le=1)
    required_consecutive: int = Field(default=2, ge=1, le=20)
    settle_timeout_seconds: float = Field(default=4.0, ge=0.05, le=60)
    sample_interval_seconds: float = Field(default=0.25, ge=0.05, le=10)
    terminal_condition: EvidenceTerminalCondition | None = None
    dynamic_scene_profile: EvidenceDynamicSceneProfile | None = None

    @model_validator(mode="after")
    def dynamic_scene_requires_terminal(self) -> "EvidenceStepBody":
        if self.dynamic_scene_profile is not None and self.terminal_condition is None:
            raise ValueError(
                "dynamic scene profile requires an explicit terminal condition"
            )
        return self


class EvidenceRunCompleteBody(BaseModel):
    lease_token: str


def _adjudication_path(facility: GameObservatory, evidence_run_id: str) -> Path:
    digest = hashlib.sha256(evidence_run_id.encode("utf-8")).hexdigest()
    return facility.store.root / "adjudications" / f"{digest}.json"


class EmergencyStopBody(BaseModel):
    target_id: str
    reason: str
    actor: str


class ClearStopBody(BaseModel):
    target_id: str
    actor: str


class RateLimitBody(BaseModel):
    target_id: str
    actor: str
    max_actions_per_minute: int = Field(default=30, ge=1, le=600)
    min_action_interval_ms: int = Field(default=150, ge=0, le=60_000)


class MumuControlBody(TargetLeaseBody):
    operation: str = Field(
        pattern="^(launch|shutdown|restart|show_window|hide_window)$"
    )


class MumuCloneBody(TargetLeaseBody):
    number: int = Field(default=1, ge=1, le=8)


class MumuSnapshotExportBody(TargetLeaseBody):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    compressed: bool = True


class MumuSnapshotImportBody(TargetLeaseBody):
    path: str = Field(min_length=1)
    number: int = Field(default=1, ge=1, le=8)


class PatchProposalBody(BaseModel):
    base_revision: int = Field(ge=1)
    author: str
    note: str
    operations: list[dict[str, Any]] = Field(min_length=1)


class PatchDecisionBody(BaseModel):
    reviewer: str
    reason: str | None = None


class AnnotationBody(BaseModel):
    object_id: str
    author: str
    body: str
    kind: str = Field(
        default="comment", pattern="^(comment|correction|question|source_note)$"
    )
    source_ids: list[str] = Field(default_factory=list)


class AnnotationResolveBody(BaseModel):
    reviewer: str


def _target_for_serial(gateway: DeviceGateway, serial: str) -> str:
    clean = serial.removeprefix("device://adb/").removeprefix("device://adb-tcp/")
    for target in gateway.store.list_targets():
        if (
            target.id == serial
            or target.endpoint == clean
            or target.metadata.get("serial") == clean
        ):
            return target.id
    gateway.refresh()
    for target in gateway.store.list_targets():
        if (
            target.id == serial
            or target.endpoint == clean
            or target.metadata.get("serial") == clean
        ):
            return target.id
    return f"device://adb/{clean}"


def _lease_for_mutation(
    request: Request,
    facility: GameObservatory,
    serial: str,
    token: str | None,
) -> tuple[str, str | None]:
    gateway = facility.device_gateway()
    target_id = _target_for_serial(gateway, serial)
    if not token:
        raise HTTPException(
            status_code=409, detail="device operation requires an active lease"
        )
    if token:
        try:
            lease = gateway.validate(target_id, token)
            return target_id, lease.id
        except GatewayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(
        status_code=409, detail="device operation requires an active lease"
    )


@game_observatory_router.get("/api/game-observatory/health")
def health() -> dict[str, Any]:
    facility = _facility()
    reports = facility.store.list_reports(include_drafts=True)
    schema = ReverseEngineeredGameDesignSpec.model_json_schema()
    schema_sha256 = hashlib.sha256(
        json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    migration_statuses: dict[str, int] = {}
    for report in reports:
        migration_statuses[report.migration_status] = (
            migration_statuses.get(report.migration_status, 0) + 1
        )
    return {
        "ok": True,
        "service": "game-observatory",
        "contract_version": DESIGN_SPEC_CONTRACT_V03,
        "design_spec_schema_sha256": schema_sha256,
        "counts": facility.store.counts(),
        "content": {
            "canonical": len(reports),
            "published": sum(item.status == "published" for item in reports),
            "drafts": sum(item.status != "published" for item in reports),
            "migration_statuses": migration_statuses,
        },
        "targets": facility.discover_targets(),
    }


@game_observatory_router.get("/api/game-observatory/catalog")
def catalog(
    response: Response,
    q: str = "",
    tag: str = "",
    game_id: str = "",
) -> dict[str, Any]:
    facility = _facility()
    reports = facility.store.list_reports(query=q, tag=tag, game_id=game_id)
    published_partial_entries = _published_partial_fact_entries(facility)
    partial_items = []
    for summary, bundle in published_partial_entries:
        searchable = " ".join(
            [
                summary["game_title"],
                summary["game_localized_title"],
                summary["play_title"],
                summary["coverage"],
                *summary["game_tags"],
                *summary["play_tags"],
            ]
        ).casefold()
        if q and q.casefold() not in searchable:
            continue
        combined_tags = {*summary["game_tags"], *summary["play_tags"]}
        if tag and tag not in combined_tags:
            continue
        if game_id and game_id not in {summary["game_id"], summary["game_slug"]}:
            continue
        partial_items.append(_public_partial_fact_catalog_item(summary, bundle))
    tag_counts = {
        item["tag"]: item["count"]
        for item in facility.store.list_tags(include_drafts=False)
    }
    for summary, _bundle in published_partial_entries:
        for value in {*summary["game_tags"], *summary["play_tags"]}:
            tag_counts[value] = tag_counts.get(value, 0) + 1
    payload = {
        "reports": [_public_report(report) for report in reports] + partial_items,
        "tags": [
            {"tag": value, "count": count}
            for value, count in sorted(tag_counts.items())
        ],
    }
    _set_public_cache(response, payload)
    return payload


@game_observatory_router.get("/api/game-observatory/search")
def public_search(
    response: Response, q: str = Query(min_length=1, max_length=200)
) -> dict[str, Any]:
    facility = _facility()
    reports = facility.store.list_reports()
    published_partial_entries = _published_partial_fact_entries(facility)
    payload = {
        "query": q,
        "results": [
            *_public_search_results(reports, q),
            *_partial_fact_search_results(
                published_partial_entries,
                q,
                facility=facility,
                limit=300,
            ),
        ],
    }
    payload["count"] = len(payload["results"])
    _set_public_cache(response, payload, seconds=30)
    return payload


@game_observatory_router.get("/api/game-observatory/workspace/design-specs")
def workspace_design_specs(
    request: Request,
    q: str = "",
    game_id: str = "",
    status: str = "",
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    facility = _facility()
    reports = facility.store.list_reports(
        query=q,
        game_id=game_id,
        include_drafts=True,
    )
    if status:
        reports = [report for report in reports if report.status == status]
    return {
        "contract_version": DESIGN_SPEC_CONTRACT_V03,
        "design_specs": [
            {
                "report": report.model_dump(mode="json"),
                "current_revision": facility.store.current_revision(report.id),
                "publication_issues": report.publication_issues(),
            }
            for report in reports
        ],
    }


@game_observatory_router.get("/api/game-observatory/workspace/partial-fact-bundles")
def workspace_partial_fact_bundles(
    request: Request,
    response: Response,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    facility = _facility()
    entries, errors, ignored = _partial_fact_workspace_entries(facility)
    bundles = [summary for summary, _bundle in entries]
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return {
        "schema": "game-observatory.partial-fact-workspace.v1",
        "bundles": bundles,
        "errors": errors,
        "ignored": ignored,
    }


@game_observatory_router.get("/api/game-observatory/workspace/search")
def workspace_partial_fact_search(
    request: Request,
    response: Response,
    q: str = Query(default="", max_length=200),
    game: str = Query(default="", max_length=200),
    play: str = Query(default="", max_length=200),
    content_type: str = Query(default="", alias="type", max_length=100),
    tag: str = Query(default="", max_length=100),
    platform: str = Query(default="", max_length=100),
    scope: str = Query(default="", pattern="^(|game|play)$"),
    limit: int = Query(default=300, ge=1, le=500),
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    facility = _facility()
    with facility.store.read_session():
        all_entries, errors, _ignored = _partial_fact_workspace_entries(facility)
        entries = [
            (summary, value)
            for summary, value in all_entries
            if (not game or game in {summary["game_id"], summary["game_slug"]})
            and (not play or play in {summary["play_id"], summary["play_slug"]})
        ]
        all_results = _partial_fact_search_results(
            entries,
            q,
            facility=facility,
            limit=500,
            include_all=bool(content_type or tag or platform or scope),
        )
        facet_results = _partial_fact_search_results(
            all_entries,
            "",
            facility=facility,
            limit=500,
            include_all=True,
        )
    facets = {
        "games": sorted(
            {
                (
                    str(summary["game_id"]),
                    str(summary["game_slug"]),
                    str(summary["game_title"]),
                )
                for summary, _value in all_entries
            }
        ),
        "plays": sorted(
            {
                (
                    str(summary["play_id"]),
                    str(summary["play_slug"]),
                    str(summary["play_title"]),
                    str(summary["game_id"]),
                    str(summary["game_title"]),
                )
                for summary, _value in all_entries
            }
        ),
        "types": sorted(
            {str(item.get("type") or "") for item in facet_results if item.get("type")}
        ),
        "tags": sorted(
            {
                str(value)
                for item in facet_results
                for value in item.get("tags", [])
                if str(value)
            }
        ),
        "platforms": sorted(
            {
                str(item.get("platform") or "")
                for item in facet_results
                if item.get("platform")
            }
        ),
        "scopes": ["game", "play"],
    }
    results = [
        item
        for item in all_results
        if (not content_type or item.get("type") == content_type)
        and (not tag or tag in item.get("tags", []))
        and (
            not platform
            or platform.casefold() in str(item.get("platform") or "").casefold()
        )
        and (not scope or item.get("content_scope") == scope)
    ][:limit]
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return {
        "schema": "game-observatory.partial-fact-search.v1",
        "query": q,
        "game": game,
        "play": play,
        "filters": {
            "game": game,
            "play": play,
            "type": content_type,
            "tag": tag,
            "platform": platform,
            "scope": scope,
        },
        "facets": facets,
        "results": results,
        "count": len(results),
        "errors": errors,
    }


@game_observatory_router.get("/api/game-observatory/workspace/partial-fact-bundle")
def workspace_partial_fact_bundle(
    request: Request,
    response: Response,
    path: str = Query(..., min_length=1, max_length=512),
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    facility = _facility()
    try:
        resolved = _partial_fact_path(facility, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="partial fact bundle not found")
    try:
        bundle = _load_partial_fact_bundle(resolved, facility)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    relative_path = resolved.relative_to(_partial_fact_drafts_root(facility)).as_posix()
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return {
        "schema": "game-observatory.partial-fact-workspace-detail.v1",
        "path": relative_path,
        "summary": _partial_fact_bundle_summary(relative_path, bundle),
        "bundle": bundle,
        "play_record_refs": _partial_fact_play_record_refs(
            facility, bundle, draft_path=relative_path
        ),
        "evidence_step_refs": _partial_fact_evidence_step_refs(facility, bundle),
    }


@game_observatory_router.get("/api/game-observatory/workspace/design-objects")
def workspace_design_objects(
    request: Request,
    report_id: str = "",
    object_type: str = "",
    q: str = "",
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    objects = _facility().store.list_design_objects(
        report_id=report_id,
        object_type=object_type,
        query=q,
    )
    return {"design_objects": objects}


@game_observatory_router.get(
    "/api/game-observatory/workspace/design-objects/{object_id}"
)
def workspace_design_object(
    object_id: str,
    request: Request,
    object_type: str = "",
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        value = _facility().store.get_design_object(
            object_id,
            object_type=object_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if value is None:
        raise HTTPException(status_code=404, detail="design object not found")
    return {"design_object": value}


@game_observatory_router.get("/api/game-observatory/workspace/design-relations")
def workspace_design_relations(
    request: Request,
    report_id: str = "",
    src_id: str = "",
    dst_id: str = "",
    relation: str = "",
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    relations = _facility().store.list_design_relations(
        report_id=report_id,
        src_id=src_id,
        dst_id=dst_id,
        relation=relation,
    )
    return {"design_relations": relations}


@game_observatory_router.get("/api/game-observatory/reports/{report_id}")
def report_detail(report_id: str, response: Response) -> dict[str, Any]:
    facility = _facility()
    report = facility.store.get_report(report_id, include_drafts=False)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    payload = _public_report(report)
    payload["revisions"] = facility.store.list_revisions(report.id)
    _set_public_cache(response, payload)
    return payload


@game_observatory_router.get("/api/game-observatory/reports/{report_id}/revisions")
def report_revisions(report_id: str) -> dict[str, Any]:
    facility = _facility()
    report = facility.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    return {
        "report_id": report.id,
        "revisions": facility.store.list_revisions(report.id),
    }


@game_observatory_router.get(
    "/api/game-observatory/reports/{report_id}/revisions/{revision}"
)
def report_revision(report_id: str, revision: int) -> dict[str, Any]:
    facility = _facility()
    report = facility.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    value = facility.store.get_revision(report.id, revision)
    if not value:
        raise HTTPException(status_code=404, detail="revision not found")
    return _public_report(value)


@game_observatory_router.get("/api/game-observatory/reports/{report_id}/diff")
def report_diff(
    report_id: str,
    from_revision: int = Query(..., ge=1),
    to_revision: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    facility = _facility()
    report = facility.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    revisions = facility.store.list_revisions(report.id)
    if not revisions:
        raise HTTPException(status_code=404, detail="report has no revisions")
    target_revision = to_revision or revisions[0]["revision"]
    before = facility.store.get_revision(report.id, from_revision)
    after = facility.store.get_revision(report.id, target_revision)
    if not before or not after:
        raise HTTPException(status_code=404, detail="revision not found")
    return {
        "report_id": report.id,
        "from_revision": from_revision,
        "to_revision": target_revision,
        "changes": SemanticReportCompiler.diff(before, after),
    }


@game_observatory_router.get("/api/game-observatory/runs")
def runs(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    values = _facility().store.list_runs(limit)
    return {"runs": [value.model_dump(mode="json") for value in values]}


@game_observatory_router.get("/api/game-observatory/targets")
def targets(refresh: bool = False) -> dict[str, Any]:
    return {"targets": _facility().discover_targets(refresh=refresh)}


@game_observatory_router.post("/api/game-observatory/gateway/refresh")
def refresh_gateway(
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    targets = _facility().discover_targets(refresh=True)
    return {"ok": True, "targets": targets}


@game_observatory_router.get("/api/game-observatory/gateway/events")
def gateway_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    return {"events": _facility().store.list_gateway_events(limit)}


@game_observatory_router.get("/api/game-observatory/gateway/controls/{target_id:path}")
def gateway_control_state(
    target_id: str,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    return {
        "control": _facility()
        .device_gateway()
        .control(target_id)
        .model_dump(mode="json")
    }


@game_observatory_router.post("/api/game-observatory/gateway/emergency-stop")
def emergency_stop(
    body: EmergencyStopBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        control = (
            _facility()
            .device_gateway()
            .emergency_stop(body.target_id, reason=body.reason, actor=body.actor)
        )
        return {"ok": True, "control": control.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/gateway/emergency-stop/clear")
def clear_emergency_stop(
    body: ClearStopBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        control = (
            _facility()
            .device_gateway()
            .clear_emergency_stop(body.target_id, actor=body.actor)
        )
        return {"ok": True, "control": control.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/gateway/rate-limit")
def configure_gateway_rate_limit(
    body: RateLimitBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        control = (
            _facility()
            .device_gateway()
            .configure_rate_limit(
                body.target_id,
                max_actions_per_minute=body.max_actions_per_minute,
                min_action_interval_ms=body.min_action_interval_ms,
                actor=body.actor,
            )
        )
        return {"ok": True, "control": control.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.get("/api/game-observatory/capture-sessions")
def capture_sessions(
    request: Request,
    target_id: str = "",
    limit: int = Query(default=100, ge=1, le=1000),
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    values = _facility().store.list_capture_sessions(target_id or None, limit=limit)
    return {"sessions": [item.model_dump(mode="json") for item in values]}


@game_observatory_router.get("/api/game-observatory/evidence-runs")
def evidence_runs(
    request: Request,
    target_id: str = "",
    limit: int = Query(default=100, ge=1, le=1000),
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    facility = _facility()
    values = facility.store.list_evidence_runs(target_id or None, limit=limit)
    return {
        "runs": [
            {
                **item.model_dump(mode="json"),
                "step_count": len(facility.store.list_evidence_steps(item.id)),
            }
            for item in values
        ]
    }


@game_observatory_router.get("/api/game-observatory/evidence-runs/{evidence_run_id}")
def evidence_run(
    evidence_run_id: str,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    facility = _facility()
    run = facility.store.get_evidence_run(evidence_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="evidence run not found")
    return {
        "run": run.model_dump(mode="json"),
        "steps": [
            item.model_dump(mode="json")
            for item in facility.store.list_evidence_steps(evidence_run_id)
        ],
    }


@game_observatory_router.get("/api/game-observatory/saturation-ledgers")
def saturation_ledgers(
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    facility = _facility()
    ledger_root = facility.store.root / "saturation"
    values: list[dict[str, Any]] = []
    if ledger_root.is_dir():
        for path in sorted(ledger_root.glob("*.json")):
            try:
                ledger = load_saturation_ledger(path)
                validation = validate_saturation_ledger(
                    ledger,
                    store=facility.store,
                )
                values.append(
                    {
                        "file": path.name,
                        "ledger": ledger.model_dump(mode="json", by_alias=True),
                        "validation": validation.model_dump(mode="json", by_alias=True),
                    }
                )
            except (OSError, ValueError) as exc:
                values.append(
                    {
                        "file": path.name,
                        "error": str(exc),
                    }
                )
    return {
        "ledger_root": str(ledger_root),
        "ledgers": values,
    }


@game_observatory_router.get(
    "/api/game-observatory/evidence-runs/{evidence_run_id}/adjudications"
)
def evidence_adjudications(
    evidence_run_id: str,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    facility = _facility()
    if not facility.store.get_evidence_run(evidence_run_id):
        raise HTTPException(status_code=404, detail="evidence run not found")
    path = _adjudication_path(facility, evidence_run_id)
    ledger = (
        EvidenceAdjudicationLedger.model_validate_json(path.read_text(encoding="utf-8"))
        if path.is_file()
        else EvidenceAdjudicationLedger(evidence_run_id=evidence_run_id)
    )
    if ledger.evidence_run_id != evidence_run_id:
        raise HTTPException(status_code=409, detail="adjudication ledger run mismatch")
    return {"adjudications": ledger.model_dump(mode="json", by_alias=True)}


@game_observatory_router.put(
    "/api/game-observatory/evidence-runs/{evidence_run_id}/adjudications"
)
def save_evidence_adjudications(
    evidence_run_id: str,
    body: EvidenceAdjudicationLedger,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    facility = _facility()
    run = facility.store.get_evidence_run(evidence_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="evidence run not found")
    if body.evidence_run_id != evidence_run_id:
        raise HTTPException(status_code=409, detail="adjudication ledger run mismatch")
    steps = {
        item.id: item for item in facility.store.list_evidence_steps(evidence_run_id)
    }
    seen: set[str] = set()
    for item in body.items:
        step = steps.get(item.step_id)
        if not step or step.step_index != item.step_index:
            raise HTTPException(
                status_code=409,
                detail=f"adjudication step does not belong to run: {item.step_id}",
            )
        if item.step_id in seen:
            raise HTTPException(
                status_code=409,
                detail=f"duplicate adjudication step: {item.step_id}",
            )
        seen.add(item.step_id)
        missing_artifacts = set(item.artifact_ids) - set(step.artifact_ids)
        if missing_artifacts:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"adjudication references artifacts outside step {item.step_id}: "
                    f"{sorted(missing_artifacts)}"
                ),
            )
    path = _adjudication_path(facility, evidence_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        body.model_dump_json(indent=2, by_alias=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return {
        "ok": True,
        "adjudications": body.model_dump(mode="json", by_alias=True),
    }


@game_observatory_router.get(
    "/api/game-observatory/evidence-runs/{evidence_run_id}/manifest"
)
def evidence_manifest(
    evidence_run_id: str,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    manifest = _facility().store.get_evidence_manifest(evidence_run_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="evidence manifest not found")
    return {"manifest": manifest.model_dump(mode="json")}


@game_observatory_router.post("/api/game-observatory/afk/live-design/promote")
def promote_afk_live_design(
    body: AfkLivePromotionBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        return _facility().promote_afk_live_design(Path(body.manifest_path))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/minecraft/live-design/promote")
def promote_minecraft_live_design(
    body: MinecraftLivePromotionBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        return _facility().promote_minecraft_live_design(Path(body.manifest_path))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.get("/api/game-observatory/leases")
def leases(
    request: Request,
    target_id: str = "",
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    values = _facility().store.list_leases(target_id or None)
    return {
        "leases": [
            {
                key: value
                for key, value in item.model_dump(mode="json").items()
                if key != "token"
            }
            for item in values
        ]
    }


@game_observatory_router.post("/api/game-observatory/leases/acquire")
def acquire_lease(
    body: LeaseAcquireBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        lease = (
            _facility()
            .device_gateway()
            .acquire(body.target_id, body.holder, ttl_seconds=body.ttl_seconds)
        )
        return {"ok": True, "lease": lease.model_dump(mode="json")}
    except LeaseConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/leases/renew")
def renew_lease(
    body: LeaseTokenBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        lease = (
            _facility().device_gateway().renew(body.token, ttl_seconds=body.ttl_seconds)
        )
        return {"ok": True, "lease": lease.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/leases/release")
def release_lease(
    body: LeaseTokenBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        lease = _facility().device_gateway().release(body.token)
        return {"ok": True, "lease": lease.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@game_observatory_router.get("/api/game-observatory/reports/{report_id}/patches")
def report_patches(
    report_id: str,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    values = _facility().store.list_report_patches(report_id)
    return {"patches": [item.model_dump(mode="json") for item in values]}


@game_observatory_router.post("/api/game-observatory/reports/{report_id}/patches")
def propose_report_patch(
    report_id: str,
    body: PatchProposalBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    from .editorial import EditorialError, EditorialService
    from .models import ReportPatchOperation

    try:
        patch = EditorialService(_facility().store).propose_patch(
            report_id,
            base_revision=body.base_revision,
            author=body.author,
            note=body.note,
            operations=[
                ReportPatchOperation.model_validate(item) for item in body.operations
            ],
        )
        return {"ok": True, "patch": patch.model_dump(mode="json")}
    except (EditorialError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/patches/{patch_id}/apply")
def apply_report_patch(
    patch_id: str,
    body: PatchDecisionBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_reviewer(request, x_game_observatory_token)
    from .editorial import EditorialError, EditorialService, PatchConflict

    facility = _facility()
    try:
        patch = EditorialService(facility.store).apply_patch(
            patch_id, reviewer=body.reviewer
        )
        build = facility.compile_public()
        return {
            "ok": True,
            "patch": patch.model_dump(mode="json"),
            "public_build": build,
        }
    except PatchConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EditorialError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/patches/{patch_id}/reject")
def reject_report_patch(
    patch_id: str,
    body: PatchDecisionBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_reviewer(request, x_game_observatory_token)
    from .editorial import EditorialError, EditorialService

    if not body.reason:
        raise HTTPException(status_code=400, detail="rejection reason is required")
    try:
        patch = EditorialService(_facility().store).reject_patch(
            patch_id, reviewer=body.reviewer, reason=body.reason
        )
        return {"ok": True, "patch": patch.model_dump(mode="json")}
    except EditorialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.get("/api/game-observatory/reports/{report_id}/annotations")
def report_annotations(
    report_id: str,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    values = _facility().store.list_report_annotations(report_id)
    return {"annotations": [item.model_dump(mode="json") for item in values]}


@game_observatory_router.post("/api/game-observatory/reports/{report_id}/annotations")
def create_report_annotation(
    report_id: str,
    body: AnnotationBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    from .editorial import EditorialError, EditorialService

    try:
        annotation = EditorialService(_facility().store).annotate(
            report_id,
            object_id=body.object_id,
            author=body.author,
            body=body.body,
            kind=body.kind,
            source_ids=body.source_ids,
        )
        return {"ok": True, "annotation": annotation.model_dump(mode="json")}
    except (EditorialError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post(
    "/api/game-observatory/annotations/{annotation_id}/resolve"
)
def resolve_report_annotation(
    annotation_id: str,
    body: AnnotationResolveBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_reviewer(request, x_game_observatory_token)
    from .editorial import EditorialError, EditorialService

    try:
        annotation = EditorialService(_facility().store).resolve_annotation(
            annotation_id, reviewer=body.reviewer
        )
        return {"ok": True, "annotation": annotation.model_dump(mode="json")}
    except EditorialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.get("/api/game-observatory/source-snapshots")
def source_snapshots(
    request: Request,
    source_id: str = "",
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    values = _facility().store.list_source_snapshots(source_id or None)
    return {"snapshots": [item.model_dump(mode="json") for item in values]}


@game_observatory_router.get("/api/game-observatory/voice-records")
def voice_records(
    request: Request,
    report_id: str = "",
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    values = _facility().store.list_voice_records(report_id or None)
    return {"voices": [item.model_dump(mode="json") for item in values]}


@game_observatory_router.get("/api/game-observatory/reports/{report_id}/voice-themes")
def voice_themes(report_id: str, response: Response) -> dict[str, Any]:
    try:
        result = SourceVoicePipeline(_facility().store).theme_view(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _set_public_cache(response, result)
    return result


@game_observatory_router.post("/api/game-observatory/sources/ingest")
def ingest_source(
    body: SourceIngestBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        facility = _facility()
        return SourceVoicePipeline(facility.store).ingest_source(
            body.report_id,
            body.source,
            excerpt=body.excerpt,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/sources/acquire")
def acquire_source(
    body: SourceAcquireBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        return SourceVoicePipeline(_facility().store).acquire_and_ingest_source(
            body.report_id,
            body.source,
            excerpt=body.excerpt,
            acquisition_url=body.acquisition_url,
            metadata=body.metadata,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/voices/ingest")
def ingest_voice(
    body: VoiceIngestBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        facility = _facility()
        return SourceVoicePipeline(facility.store).ingest_player_voice(
            body.report_id,
            body.source,
            body.voice,
            excerpt=body.excerpt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/community-feedback/acquire")
def acquire_community_feedback(
    body: CommunityFeedbackIngestBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        return SourceVoicePipeline(
            _facility().store
        ).acquire_and_ingest_community_feedback(
            body.report_id,
            body.feedback,
            excerpt=body.excerpt,
            acquisition_url=body.acquisition_url,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post(
    "/api/game-observatory/reports/{report_id}/voices/{voice_id}/review"
)
def review_voice(
    report_id: str,
    voice_id: str,
    body: VoiceReviewBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_reviewer(request, x_game_observatory_token)
    try:
        return SourceVoicePipeline(_facility().store).review_player_voice(
            report_id,
            voice_id,
            decision=body.decision,
            reviewer=body.reviewer,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/sources/{source_id}/retract")
def retract_source(
    source_id: str,
    body: RetractionBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        return SourceVoicePipeline(_facility().store).retract_source(
            source_id, body.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/bootstrap")
def bootstrap(
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    return _facility().bootstrap()


@game_observatory_router.post("/api/game-observatory/reports")
def upsert_report(
    report: GameReport,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    role = _require_editor(request, x_game_observatory_token)
    if role == "author" and report.status == "published":
        raise HTTPException(
            status_code=403,
            detail="authors submit reports to review; reviewers publish",
        )
    facility = _facility()
    try:
        if report.status == "published":
            report.assert_publishable()
        else:
            report.assert_storable()
        facility.store.upsert_report(report)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    facility.store.export_reports(facility.store.list_reports())
    facility.compile_public()
    return {
        "ok": True,
        "report_id": report.id,
        "status": report.status,
        "revisions": facility.store.list_revisions(report.id),
    }


@game_observatory_router.post("/api/game-observatory/reports/{report_id}/transition")
def transition_report(
    report_id: str,
    body: ReviewTransitionBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    role = _require_editor(request, x_game_observatory_token)
    facility = _facility()
    report = facility.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    current = report.status
    allowed = {
        "author": {("draft", "review"), ("review", "draft")},
        "reviewer": {
            ("review", "published"),
            ("published", "review"),
            ("review", "draft"),
        },
        "admin": {
            ("draft", "review"),
            ("review", "draft"),
            ("review", "published"),
            ("published", "review"),
            ("draft", "published"),
            ("published", "draft"),
        },
    }
    if current != body.status and (current, body.status) not in allowed[role]:
        raise HTTPException(
            status_code=403,
            detail=f"role {role} cannot transition {current} -> {body.status}",
        )
    candidate = report.model_copy(deep=True)
    candidate.status = body.status
    if body.status == "published":
        try:
            candidate.assert_publishable()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        candidate.migration_status = "publishable"
    else:
        if current == "published" and body.status == "review":
            candidate.migration_status = "review_ready"
        if body.status == "draft" and candidate.migration_status == "publishable":
            candidate.migration_status = "needs_evidence"
        candidate.assert_storable()
    from .models import utc_now

    candidate.updated_at = utc_now()
    try:
        facility.store.upsert_report(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    facility.store.export_reports(facility.store.list_reports())
    facility.compile_public()
    facility.store.append_gateway_event(
        "report_transition",
        None,
        {
            "report_id": report.id,
            "from": current,
            "to": body.status,
            "role": role,
            "note": body.note,
        },
    )
    return {
        "ok": True,
        "report_id": report.id,
        "from": current,
        "to": candidate.status,
        "role": role,
        "revisions": facility.store.list_revisions(report.id),
    }


@game_observatory_router.get("/api/game-observatory/schemas/report")
def report_schema() -> dict[str, Any]:
    return GameReport.model_json_schema()


@game_observatory_router.get("/api/game-observatory/schemas/design-spec")
def design_spec_schema() -> dict[str, Any]:
    return ReverseEngineeredGameDesignSpec.model_json_schema()


@game_observatory_router.get("/api/game-observatory/schemas/evidence-run")
def evidence_run_schema() -> dict[str, Any]:
    return EvidenceRun.model_json_schema()


@game_observatory_router.get("/api/game-observatory/schemas/evidence-step")
def evidence_step_schema() -> dict[str, Any]:
    return EvidenceStep.model_json_schema()


@game_observatory_router.get("/api/game-observatory/schemas/evidence-manifest")
def evidence_manifest_schema() -> dict[str, Any]:
    return EvidenceRunManifest.model_json_schema()


@game_observatory_router.post("/api/game-observatory/capture")
def capture(
    body: CaptureBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        facility = _facility()
        target_id, lease_id = _lease_for_mutation(
            request, facility, body.serial, body.lease_token
        )
        facility.device_gateway().assert_operational(target_id)
        result = facility.capture_device(body.serial)
        facility.store.append_gateway_event(
            "capture",
            target_id,
            {"lease_id": lease_id, "observation": result["observation"]},
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/actions")
def perform_action(
    body: ActionBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    del body
    raise HTTPException(
        status_code=409,
        detail=(
            "bare game actions are disabled; open an evidence run and append an evidence step"
        ),
    )


@game_observatory_router.post("/api/game-observatory/evidence-runs")
def start_evidence_run(
    body: EvidenceRunStartBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        run = (
            _facility()
            .device_gateway()
            .start_evidence_run(
                body.target_id,
                body.lease_token,
                viewport_width=body.viewport_width,
                viewport_height=body.viewport_height,
                game_id=body.game_id,
                build_scope_id=body.build_scope_id,
                scope_id=body.scope_id,
                environment=body.environment,
            )
        )
        return {"ok": True, "run": run.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post(
    "/api/game-observatory/evidence-runs/{evidence_run_id}/steps"
)
def append_evidence_step(
    evidence_run_id: str,
    body: EvidenceStepBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        step = (
            _facility()
            .device_gateway()
            .record_evidence_step(
                evidence_run_id,
                body.lease_token,
                body.action,
                target_name=body.target_name,
                target_bounds=body.target_bounds,
                settle_threshold=body.settle_threshold,
                required_consecutive=body.required_consecutive,
                settle_timeout_seconds=body.settle_timeout_seconds,
                sample_interval_seconds=body.sample_interval_seconds,
                terminal_condition=body.terminal_condition,
                dynamic_scene_profile=body.dynamic_scene_profile,
            )
        )
        return {"ok": step.status == "passed", "step": step.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post(
    "/api/game-observatory/evidence-runs/{evidence_run_id}/pause"
)
def pause_evidence_run(
    evidence_run_id: str,
    body: EvidenceRunCompleteBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        run = (
            _facility()
            .device_gateway()
            .pause_evidence_run(
                evidence_run_id,
                body.lease_token,
            )
        )
        return {"ok": True, "run": run.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post(
    "/api/game-observatory/evidence-runs/{evidence_run_id}/resume"
)
def resume_evidence_run(
    evidence_run_id: str,
    body: EvidenceRunCompleteBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        run = (
            _facility()
            .device_gateway()
            .resume_evidence_run(
                evidence_run_id,
                body.lease_token,
            )
        )
        return {"ok": True, "run": run.model_dump(mode="json")}
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post(
    "/api/game-observatory/evidence-runs/{evidence_run_id}/complete"
)
def complete_evidence_run(
    evidence_run_id: str,
    body: EvidenceRunCompleteBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        manifest = (
            _facility()
            .device_gateway()
            .complete_evidence_run(
                evidence_run_id,
                body.lease_token,
            )
        )
        return {
            "ok": manifest.publishable,
            "manifest": manifest.model_dump(mode="json"),
        }
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/capture-stream")
def capture_stream(
    body: CaptureStreamBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        session = (
            _facility()
            .device_gateway()
            .capture_stream(
                body.target_id,
                body.lease_token,
                frame_count=body.frame_count,
                interval_seconds=body.interval_seconds,
                include_ui_every=body.include_ui_every,
                max_recoveries=body.max_recoveries,
            )
        )
        return {
            "ok": session.status == "passed",
            "session": session.model_dump(mode="json"),
        }
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/packages/install")
def install_apk(
    body: InstallApkBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        result = (
            _facility()
            .device_gateway()
            .install_apk(body.target_id, body.lease_token, Path(body.apk_path))
        )
        return result
    except (AdapterError, GatewayError, ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/packages/start")
def start_package(
    body: PackageOperationBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_editor(request, x_game_observatory_token)
    try:
        return (
            _facility()
            .device_gateway()
            .start_package(body.target_id, body.lease_token, body.package)
        )
    except (AdapterError, GatewayError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/packages/force-stop")
def force_stop_package(
    body: PackageOperationBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        return (
            _facility()
            .device_gateway()
            .force_stop_package(body.target_id, body.lease_token, body.package)
        )
    except (AdapterError, GatewayError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/mumu/control")
def control_mumu(
    body: MumuControlBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        return (
            _facility()
            .device_gateway()
            .mumu_control(body.target_id, body.lease_token, body.operation)
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/mumu/clone")
def clone_mumu(
    body: MumuCloneBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        return (
            _facility()
            .device_gateway()
            .mumu_clone(body.target_id, body.lease_token, number=body.number)
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/mumu/snapshots/export")
def export_mumu_snapshot(
    body: MumuSnapshotExportBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        return (
            _facility()
            .device_gateway()
            .mumu_export_snapshot(
                body.target_id,
                body.lease_token,
                name=body.name,
                compressed=body.compressed,
            )
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/mumu/snapshots/import")
def import_mumu_snapshot(
    body: MumuSnapshotImportBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        return (
            _facility()
            .device_gateway()
            .mumu_import_snapshot(
                body.target_id,
                body.lease_token,
                Path(body.path),
                number=body.number,
            )
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.post("/api/game-observatory/mumu/delete-clone")
def delete_mumu_clone(
    body: TargetLeaseBody,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request, x_game_observatory_token)
    try:
        return (
            _facility()
            .device_gateway()
            .mumu_delete_clone(body.target_id, body.lease_token)
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@game_observatory_router.get("/api/game-observatory/artifacts/{artifact_id}")
def artifact_file(artifact_id: str) -> FileResponse:
    facility = _facility()
    artifact = facility.store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    if not artifact.metadata.get(
        "public"
    ) and artifact_id not in _published_partial_fact_image_artifact_ids(facility):
        raise HTTPException(status_code=404, detail="artifact is not public")
    path = Path(artifact.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="artifact file is missing")
    return FileResponse(
        path,
        media_type=artifact.media_type,
        headers={
            "ETag": f'"{artifact.sha256}"',
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@game_observatory_router.get("/api/game-observatory/internal/artifacts/{artifact_id}")
def internal_artifact_file(
    artifact_id: str,
    request: Request,
    x_game_observatory_token: str | None = Header(default=None),
) -> FileResponse:
    _require_editor(request, x_game_observatory_token)
    artifact = _facility().store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="artifact file is missing")
    return FileResponse(
        path,
        media_type=artifact.media_type,
        headers={
            "ETag": f'"{artifact.sha256}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@game_observatory_router.get(
    "/api/game-observatory/internal/artifacts/{artifact_id}/thumbnail"
)
def internal_artifact_thumbnail(
    artifact_id: str,
    request: Request,
    max_width: int = Query(default=360, ge=64, le=640),
    max_height: int = Query(default=240, ge=64, le=640),
    x_game_observatory_token: str | None = Header(default=None),
) -> Response:
    """Return a bounded thumbnail while keeping the immutable original separately reachable."""

    _require_editor(request, x_game_observatory_token)
    artifact = _facility().store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="artifact file is missing")
    if artifact.kind not in {"screenshot", "video_frame", "annotated_plate"} and not (
        artifact.media_type and artifact.media_type.startswith("image/")
    ):
        raise HTTPException(status_code=415, detail="artifact is not a thumbnail image")
    try:
        content = _internal_thumbnail_bytes(
            str(path.resolve()),
            artifact.sha256,
            max_width,
            max_height,
        )
    except (OSError, UnidentifiedImageError) as exc:
        raise HTTPException(
            status_code=422, detail="artifact image cannot be decoded"
        ) from exc
    thumbnail_etag = hashlib.sha256(
        f"{artifact.sha256}:{max_width}:{max_height}:webp80".encode("ascii")
    ).hexdigest()
    return Response(
        content=content,
        media_type="image/webp",
        headers={
            "ETag": f'"{thumbnail_etag}"',
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "X-Thumbnail-Max-Width": str(max_width),
            "X-Thumbnail-Max-Height": str(max_height),
            "X-Source-SHA256": artifact.sha256,
        },
    )


@game_observatory_router.get("/game-observatory", response_class=HTMLResponse)
@game_observatory_router.get("/game-observatory/", response_class=HTMLResponse)
@game_observatory_router.get(
    "/game-observatory/report/{report_slug}", response_class=HTMLResponse
)
@game_observatory_router.get(
    "/game-observatory/game/{report_slug}", response_class=HTMLResponse
)
@game_observatory_router.get(
    "/game-observatory/play/{report_slug}", response_class=HTMLResponse
)
def public_site(report_slug: str | None = None) -> HTMLResponse:
    document = _public_site_document()
    return HTMLResponse(
        document,
        headers={
            "Cache-Control": "public, max-age=0, must-revalidate",
            "ETag": f'"{hashlib.sha256(document.encode("utf-8")).hexdigest()}"',
            "Content-Security-Policy": _PUBLIC_CSP,
        },
    )


@game_observatory_router.get("/game-observatory/live", response_class=HTMLResponse)
@game_observatory_router.get("/game-observatory/live/", response_class=HTMLResponse)
def ai_player_live_site() -> HTMLResponse:
    document = _live_document()
    return HTMLResponse(
        document,
        headers={
            "Cache-Control": "private, max-age=0, must-revalidate",
            "ETag": f'"{hashlib.sha256(document.encode("utf-8")).hexdigest()}"',
            "Content-Security-Policy": _PUBLIC_CSP,
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@game_observatory_router.get("/game-observatory/studio", response_class=HTMLResponse)
@game_observatory_router.get("/game-observatory/studio/", response_class=HTMLResponse)
@game_observatory_router.get(
    "/game-observatory/studio/{surface}", response_class=HTMLResponse
)
@game_observatory_router.get("/game-observatory/console", response_class=HTMLResponse)
def studio_site(surface: str | None = None) -> HTMLResponse:
    del surface
    document = _studio_document()
    return HTMLResponse(
        document,
        headers={
            "Cache-Control": "private, max-age=0, must-revalidate",
            "ETag": f'"{hashlib.sha256(document.encode("utf-8")).hexdigest()}"',
            "Content-Security-Policy": _PUBLIC_CSP,
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@game_observatory_router.get(
    "/game-observatory/reports/{report_slug}", response_class=HTMLResponse
)
def semantic_report(report_slug: str) -> HTMLResponse:
    facility = _facility()
    report = facility.store.get_report(report_slug, include_drafts=False)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    content = SemanticReportCompiler.semantic_html(report)
    return HTMLResponse(
        content,
        headers={
            "ETag": _etag(_public_report(report)),
            "Cache-Control": "public, max-age=300, stale-while-revalidate=1800",
            "Content-Security-Policy": _PUBLIC_CSP,
        },
    )


@game_observatory_router.get("/api/game-observatory/diagrams/{report_slug}/{kind}.svg")
def design_diagram(report_slug: str, kind: str) -> Response:
    report = _facility().store.get_report(report_slug, include_drafts=False)
    if not report:
        raise HTTPException(status_code=404, detail="design spec not found")
    try:
        content = SemanticReportCompiler.diagram_svg(report, kind)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="image/svg+xml",
        headers={
            "ETag": _etag({"report": report.updated_at, "kind": kind}),
            "Cache-Control": "public, max-age=300, stale-while-revalidate=1800",
            "X-Content-Type-Options": "nosniff",
        },
    )


@game_observatory_router.get("/game-observatory/sitemap.xml")
def public_sitemap() -> FileResponse:
    facility = _facility()
    path = facility.store.export_root / "public" / "sitemap.xml"
    if not path.is_file():
        facility.compile_public()
    return FileResponse(
        path,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=300"},
    )


@game_observatory_router.get("/robots.txt")
def robots() -> PlainTextResponse:
    return PlainTextResponse(
        "User-agent: *\nAllow: /game-observatory/\nSitemap: /game-observatory/sitemap.xml\n"
    )


@game_observatory_router.get("/api/game-observatory/fragments/{claim_id}")
def claim_fragment(claim_id: str, response: Response) -> dict[str, Any]:
    for report in _facility().store.list_reports():
        public = _public_report(report)
        claim = next(
            (item for item in public["claims"] if item["id"] == claim_id), None
        )
        if claim:
            payload = {
                "claim": claim,
                "report_id": report.id,
                "report_slug": report.slug,
                "url": f"/game-observatory/reports/{report.slug}#{claim_id}",
            }
            _set_public_cache(response, payload, seconds=300)
            return payload
    raise HTTPException(status_code=404, detail="claim fragment not found")


@game_observatory_router.get("/game-observatory/app.js")
def public_app_js() -> FileResponse:
    return FileResponse(
        _WEB / "app.js",
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@game_observatory_router.get("/game-observatory/studio.js")
def studio_app_js() -> FileResponse:
    return FileResponse(
        _WEB / "studio.js",
        media_type="text/javascript",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


@game_observatory_router.get("/game-observatory/studio.css")
def studio_styles() -> FileResponse:
    return FileResponse(
        _WEB / "studio.css",
        media_type="text/css",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


@game_observatory_router.get("/game-observatory/live.js")
def live_app_js() -> FileResponse:
    return FileResponse(
        _WEB / "live.js",
        media_type="text/javascript",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


@game_observatory_router.get("/game-observatory/live.css")
def live_styles() -> FileResponse:
    return FileResponse(
        _WEB / "live.css",
        media_type="text/css",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


@game_observatory_router.get("/game-observatory/styles.css")
def public_styles() -> FileResponse:
    return FileResponse(
        _WEB / "styles.css",
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
