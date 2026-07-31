"""LAN AI-player live-room projection, frame broker, and spectator instruction gate."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from ..adapters import AdbAdapter, AdapterError
from .console_projection import build_ai_player_console_projection
from .external_action_quality import verified_external_usage_increment
from .external_agent_runtime import ExternalAgentSessionLedger
from .store import AIPlayerStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_text(value: str, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    return compact[:limit]


class SpectatorInstructionSubmissionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str = Field(min_length=1, max_length=512)
    session_id: str | None = Field(default=None, min_length=3, max_length=256)
    display_name: str = Field(default="局域网观众", min_length=1, max_length=40)
    instruction: str = Field(min_length=1, max_length=500)


class SpectatorInstructionReviewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    normalized_instruction: str | None = Field(default=None, max_length=1200)
    reason: str = Field(min_length=1, max_length=500)
    reviewer: Literal["game-domain-reviewer-agent.v1"] = "game-domain-reviewer-agent.v1"
    policy_version: Literal["spectator-game-only.v1"] = "spectator-game-only.v1"


class SpectatorInstructionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    environment_id: str
    session_id: str | None
    display_name: str
    submitted_by: str
    raw_instruction: str
    normalized_instruction: str | None
    status: Literal["approved", "rejected", "delivered"]
    review_reason: str
    reviewer: str
    policy_version: str
    submitted_at: str
    reviewed_at: str
    delivered_at: str | None = None
    delivery_round_id: str | None = None


class GameDomainReviewerAgent:
    """Fail-closed reviewer/translator for untrusted LAN spectator instructions."""

    _COMMAND_OR_EXTERNAL = re.compile(
        r"(?i)(?:"
        r"\b(?:cmd|powershell|pwsh|bash|shell|terminal|python|pip|npm|node|git|curl|wget|"
        r"http|https|sql|sqlite|docker|kubectl|ssh|scp|exec|subprocess)\b|"
        r"命令行|终端|脚本|代码|仓库|文件|目录|网页|浏览器|下载|上传|删除文件|修改文件|"
        r"发邮件|collab platform|微信|钉钉|查询天气|写文章|做表格|生成图片"
        r")"
    )
    _REAL_MONEY_OR_IDENTITY = re.compile(
        r"(?:充值|真实支付|付款|购买礼包|购买月卡|购买通行证|扫码支付|"
        r"实名认证|身份证|人脸识别|提交身份|绑定银行卡|账号出售|账号转移)"
    )
    _RAW_CONTROL = re.compile(
        r"(?i)(?:omni\s+game\s+player|adb(?:\.exe)?\s|"
        r"(?:tap|swipe|click)\s+\d+[\s,]+\d+|[;&|`$<>])"
    )
    _GAME_TERMS = re.compile(
        r"(?:游戏|当前画面|任务|章节|主线|支线|讨伐|军演|远征|战斗|守军|"
        r"开垦|占领|打地|屯田|征兵|预备兵|部队|阵容|武将|战法|装备|"
        r"城建|君王殿|征兵所|军营|研究所|资源|木材|石料|铁矿|黄金|"
        r"招募|抽卡|背包|活动|奖励|同盟|地图|地块|回城|前往|返回|"
        r"探索|观察|查看|继续|推进|升级|挑战|收集|领取|等待|退出)"
    )

    def review(self, instruction: str) -> SpectatorInstructionReviewV1:
        raw = _compact_text(instruction, limit=500)
        if not raw:
            return self._reject("指示为空。")
        if self._COMMAND_OR_EXTERNAL.search(raw):
            return self._reject("指示包含终端、文件、网络或其他非游戏工作。")
        if self._RAW_CONTROL.search(raw):
            return self._reject("观众不能下发原始命令、坐标或控制字符。")
        if self._REAL_MONEY_OR_IDENTITY.search(raw):
            return self._reject("真实支付和外部身份提交是硬停止边界。")
        if not self._GAME_TERMS.search(raw):
            return self._reject("无法确认这是当前游戏内目标；请改写成明确的游戏目的。")

        translated = (
            "观众指示（已经 game-domain-reviewer-agent.v1 二层审核）："
            f"{raw}。只允许在当前游戏、当前账号和既有 omni game player 设施内完成；"
            "不得运行与游戏无关的终端、文件、网络或仓库命令；"
            "不得进行真实支付或向外部服务提交身份资料；"
            "把它作为本轮候选目的，与既有账号安全策略、任务退出条件和现场证据共同判断。"
        )
        return SpectatorInstructionReviewV1(
            decision="approved",
            normalized_instruction=translated,
            reason="已确认是游戏域自然语言目的，并补齐设施与账号红线。",
        )

    @staticmethod
    def _reject(reason: str) -> SpectatorInstructionReviewV1:
        return SpectatorInstructionReviewV1(
            decision="rejected",
            reason=reason,
        )


class LiveInstructionStore:
    """Independent append-only queue consumed only against completed round boundaries."""

    def __init__(self, observatory_root: Path) -> None:
        root = observatory_root.resolve() / "live_facility"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "instructions.sqlite3"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS spectator_instructions (
                    id TEXT PRIMARY KEY,
                    environment_id TEXT NOT NULL,
                    session_id TEXT,
                    display_name TEXT NOT NULL,
                    submitted_by TEXT NOT NULL,
                    raw_instruction TEXT NOT NULL,
                    normalized_instruction TEXT,
                    status TEXT NOT NULL CHECK(status IN ('approved','rejected','delivered')),
                    review_reason TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    delivered_at TEXT,
                    delivery_round_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_spectator_instruction_queue
                    ON spectator_instructions(environment_id, session_id, status, submitted_at);
                CREATE TABLE IF NOT EXISTS ai_player_round_boundaries (
                    environment_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    round_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    consumed_instruction_id TEXT,
                    PRIMARY KEY(environment_id, session_id, round_id)
                );
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> SpectatorInstructionV1:
        return SpectatorInstructionV1(**dict(row))

    def submit(
        self,
        submission: SpectatorInstructionSubmissionV1,
        *,
        submitted_by: str,
        reviewer: GameDomainReviewerAgent | None = None,
    ) -> SpectatorInstructionV1:
        review = (reviewer or GameDomainReviewerAgent()).review(submission.instruction)
        now = _utc_now()
        item_id = f"spectator-instruction.{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            pending = connection.execute(
                """
                SELECT COUNT(*) FROM spectator_instructions
                WHERE environment_id=? AND submitted_by=? AND status='approved'
                """,
                (submission.environment_id, submitted_by),
            ).fetchone()[0]
            if pending >= 5:
                raise ValueError("该观众已有 5 条待发送指示，请等待 AI 完成下一整轮。")
            connection.execute(
                """
                INSERT INTO spectator_instructions(
                    id,environment_id,session_id,display_name,submitted_by,
                    raw_instruction,normalized_instruction,status,review_reason,
                    reviewer,policy_version,submitted_at,reviewed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item_id,
                    submission.environment_id,
                    submission.session_id,
                    _compact_text(submission.display_name, limit=40),
                    _compact_text(submitted_by, limit=120),
                    _compact_text(submission.instruction, limit=500),
                    review.normalized_instruction,
                    review.decision,
                    review.reason,
                    review.reviewer,
                    review.policy_version,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM spectator_instructions WHERE id=?",
                (item_id,),
            ).fetchone()
        assert row is not None
        return self._row(row)

    def list(
        self,
        environment_id: str,
        *,
        session_id: str | None = None,
        limit: int = 30,
    ) -> list[SpectatorInstructionV1]:
        query = "SELECT * FROM spectator_instructions WHERE environment_id=?"
        params: list[Any] = [environment_id]
        if session_id is not None:
            query += " AND (session_id IS NULL OR session_id=?)"
            params.append(session_id)
        query += " ORDER BY submitted_at DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row(row) for row in rows]

    def record_round_boundary(
        self,
        *,
        environment_id: str,
        session_id: str,
        round_id: str,
        completed_at: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_player_round_boundaries(
                    environment_id,session_id,round_id,completed_at
                ) VALUES(?,?,?,?)
                """,
                (environment_id, session_id, round_id, completed_at or _utc_now()),
            )

    def latest_open_round_id(
        self,
        *,
        environment_id: str,
        session_id: str,
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT round_id FROM ai_player_round_boundaries
                WHERE environment_id=? AND session_id=?
                  AND consumed_instruction_id IS NULL
                ORDER BY completed_at DESC LIMIT 1
                """,
                (environment_id, session_id),
            ).fetchone()
        return str(row["round_id"]) if row is not None else None

    def deliver_next(
        self,
        *,
        environment_id: str,
        session_id: str,
        after_round_id: str,
    ) -> SpectatorInstructionV1 | None:
        """Consume at most one approved instruction for one unconsumed completed round."""

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            boundary = connection.execute(
                """
                SELECT consumed_instruction_id FROM ai_player_round_boundaries
                WHERE environment_id=? AND session_id=? AND round_id=?
                """,
                (environment_id, session_id, after_round_id),
            ).fetchone()
            if boundary is None:
                raise ValueError("指示只能在已登记的完整 AI 轮次边界领取。")
            if boundary["consumed_instruction_id"] is not None:
                connection.commit()
                return None
            row = connection.execute(
                """
                SELECT * FROM spectator_instructions
                WHERE environment_id=? AND status='approved'
                  AND (session_id IS NULL OR session_id=?)
                ORDER BY submitted_at ASC LIMIT 1
                """,
                (environment_id, session_id),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = _utc_now()
            connection.execute(
                """
                UPDATE spectator_instructions
                SET status='delivered', delivered_at=?, delivery_round_id=?
                WHERE id=? AND status='approved'
                """,
                (now, after_round_id, row["id"]),
            )
            connection.execute(
                """
                UPDATE ai_player_round_boundaries SET consumed_instruction_id=?
                WHERE environment_id=? AND session_id=? AND round_id=?
                  AND consumed_instruction_id IS NULL
                """,
                (row["id"], environment_id, session_id, after_round_id),
            )
            delivered = connection.execute(
                "SELECT * FROM spectator_instructions WHERE id=?",
                (row["id"],),
            ).fetchone()
            connection.commit()
        assert delivered is not None
        return self._row(delivered)


def compose_round_instruction(
    base_instruction: str | None,
    spectator_instruction: SpectatorInstructionV1 | None,
) -> str | None:
    if spectator_instruction is None:
        return base_instruction
    values = [value.strip() for value in (base_instruction, spectator_instruction.normalized_instruction) if value and value.strip()]
    return "\n\n".join(values) or None


def _slim_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("id", "title", "description", "status", "version", "created_at", "tags", "visuals")
        if value.get(key) is not None
    }


def _message_summary(message: str) -> dict[str, Any]:
    stop = re.search(r"(?m)^A2_TURN_STOP_REASON=(.+)$", message)
    actions = re.search(r"(?m)^A2_ACTIONS_EXECUTED=(\d+)$", message)
    summary = re.search(r"(?m)^A2_MICRO_STAGE_SUMMARY=(.+)$", message)
    return {
        "decision_summary": _compact_text(summary.group(1), limit=500) if summary else None,
        "stop_reason": _compact_text(stop.group(1), limit=80) if stop else None,
        "actions_executed": int(actions.group(1)) if actions else None,
        "agent_output": message[-4000:],
    }


def _read_session_message(
    ledger: ExternalAgentSessionLedger,
    relative_path: str,
) -> str:
    candidate = (ledger.root / relative_path).resolve()
    try:
        candidate.relative_to(ledger.root)
    except ValueError:
        return ""
    if not candidate.is_file():
        return ""
    return candidate.read_text(encoding="utf-8", errors="replace")


def _external_session_projection(
    ledger: ExternalAgentSessionLedger,
    *,
    environment_id: str,
    session_id: str | None,
) -> dict[str, Any] | None:
    sessions = [
        item
        for item in ledger.list_sessions()
        if item.environment_id == environment_id and (session_id is None or item.id == session_id)
    ]
    if not sessions:
        return None
    session = sessions[0]
    heartbeat = ledger.read_heartbeat(session.id)
    effective_status = (
        "suspended" if session.status in {"starting", "active"} else session.status
    )
    status_source = (
        "no_fresh_provider_heartbeat"
        if session.status in {"starting", "active"}
        else "canonical_session"
    )
    if isinstance(heartbeat, dict):
        try:
            heartbeat_at = datetime.fromisoformat(
                str(heartbeat["timestamp"]).replace("Z", "+00:00")
            )
            heartbeat_sequence = int(heartbeat["sequence"])
            heartbeat_age_seconds = (
                datetime.now(timezone.utc) - heartbeat_at
            ).total_seconds()
        except (KeyError, TypeError, ValueError):
            heartbeat_age_seconds = None
            heartbeat_sequence = None
        if (
            heartbeat_age_seconds is not None
            and -5 <= heartbeat_age_seconds <= 60
            and heartbeat_sequence is not None
            and heartbeat_sequence > session.invocation_count
        ):
            effective_status = "active"
            status_source = "fresh_provider_heartbeat"
    invocations = ledger.list_invocations(session.id)
    turns: list[dict[str, Any]] = []
    reported_costs: list[float] = []
    for invocation in invocations[-20:]:
        verified_usage = verified_external_usage_increment(ledger, invocation)
        usage = (
            verified_usage.model_dump(mode="json")
            if verified_usage is not None
            else {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            }
        )
        message = _read_session_message(ledger, invocation.last_message_path)
        if invocation.provider_cost_usd is not None:
            reported_costs.append(invocation.provider_cost_usd)
        turns.append(
            {
                "id": invocation.id,
                "sequence": invocation.sequence,
                "status": invocation.status,
                "started_at": invocation.started_at,
                "completed_at": invocation.completed_at,
                "duration_seconds": invocation.duration_seconds,
                "usage": usage,
                "reported_cost_usd": invocation.provider_cost_usd,
                **_message_summary(message),
            }
        )
    return {
        "session_id": session.id,
        "framework": "game-observatory.external-agent-runtime.v1",
        "provider": session.provider,
        "model_selector": session.model_selector,
        "resolved_model_id": session.resolved_model_id,
        "requested_effort": session.requested_effort,
        "actual_effort": session.actual_effort,
        "permission_mode": session.permission_mode,
        "status": effective_status,
        "status_source": status_source,
        "provider_heartbeat": {
            "sequence": heartbeat.get("sequence"),
            "timestamp": heartbeat.get("timestamp"),
            "process_id": heartbeat.get("process_id"),
        }
        if isinstance(heartbeat, dict)
        else None,
        "phase_id": session.phase_id,
        "generation": session.generation,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
        "usage": {
            "input_tokens": session.input_tokens,
            "cached_input_tokens": session.cached_input_tokens,
            "cache_creation_input_tokens": session.cache_creation_input_tokens,
            "output_tokens": session.output_tokens,
            "reasoning_tokens": session.reasoning_tokens,
        },
        "provider_runtime_seconds": session.total_duration_seconds,
        "semantic_action_count": session.semantic_action_count,
        "atomic_action_count": session.atomic_action_count,
        "cost": {
            "reported_usd": round(sum(reported_costs), 6) if reported_costs else None,
            "covered_invocations": len(reported_costs),
            "total_invocations": len(invocations),
            "source": "provider_receipts_only",
        },
        "reasoning_visibility": {
            "raw_chain_of_thought_exposed": False,
            "displayed_surface": "agent output + A2 decision summaries + canonical action receipts",
        },
        "turns": turns,
    }


def build_live_room_projection(
    player: AIPlayerStore,
    *,
    environment_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    console = build_ai_player_console_projection(player, environment_id=environment_id)
    identity = console.get("identity")
    if not isinstance(identity, dict) or not identity.get("id"):
        raise KeyError("no AI-player environment is available")
    selected_environment_id = str(identity["id"])
    ledger = ExternalAgentSessionLedger(player.observatory_store.root)
    agent = _external_session_projection(
        ledger,
        environment_id=selected_environment_id,
        session_id=session_id,
    )
    selected_session_id = str(agent["session_id"]) if agent else session_id
    instruction_store = LiveInstructionStore(player.observatory_store.root)

    evidence = console.get("evidence") if isinstance(console.get("evidence"), dict) else {}
    steps = evidence.get("steps") if isinstance(evidence.get("steps"), list) else []
    operations = [
        {
            "id": step.get("id"),
            "step_index": step.get("step_index"),
            "status": step.get("status"),
            "started_at": step.get("started_at"),
            "ended_at": step.get("ended_at"),
            "target_name": step.get("target_name"),
            "action": step.get("action"),
            "error": step.get("error"),
            "before": step.get("before"),
            "after": step.get("after"),
        }
        for step in steps[-30:]
        if isinstance(step, dict)
    ]
    last_observed_at = next(
        (
            value
            for step in reversed(operations)
            for value in (step.get("ended_at"), step.get("started_at"))
            if value
        ),
        identity.get("created_at"),
    )
    monitoring = (
        console.get("iteration_monitoring")
        if isinstance(console.get("iteration_monitoring"), dict)
        else {}
    )
    metrics = monitoring.get("account_metric_derivations")
    if not isinstance(metrics, list):
        metrics = []
    state_map = console.get("state_map") if isinstance(console.get("state_map"), dict) else {}
    nodes = state_map.get("nodes") if isinstance(state_map.get("nodes"), list) else []
    edges = state_map.get("edges") if isinstance(state_map.get("edges"), list) else []

    return {
        "schema": "game-observatory.ai-player.live-room.v1",
        "generated_at": _utc_now(),
        "refresh_after_ms": 1500,
        "room": {
            "environment_id": selected_environment_id,
            "session_id": selected_session_id,
            "game_id": identity.get("game_id"),
            "channel": identity.get("channel"),
            "device_scope_id": identity.get("device_scope_id"),
            "media_stream": {
                "kind": "webrtc",
                "provider": "mediamtx",
                "publisher": "obs-whip-nvenc",
                "path": "game-observatory",
                "webrtc_port": int(os.environ.get("OMNI_GAME_LIVE_WEBRTC_PORT", "8889")),
                "hls_port": int(os.environ.get("OMNI_GAME_LIVE_HLS_PORT", "8888")),
                "target_fps": int(os.environ.get("OMNI_GAME_LIVE_TARGET_FPS", "60")),
                "target_resolution": "1920x1080",
                "fallback": "low-latency-hls-then-mjpeg",
            },
            "stream_url": (
                "/api/game-observatory/ai-player/live/stream.mjpg?"
                f"environment_id={selected_environment_id}"
            ),
        },
        "agent": agent,
        "account": {
            "identity": {
                key: identity.get(key)
                for key in (
                    "game_id",
                    "build_scope_id",
                    "account_scope_id",
                    "channel",
                    "server_scope_id",
                    "world_scope_id",
                    "locale",
                )
            },
            "last_observed_at": last_observed_at,
            "current_state": _slim_state(console.get("current_state")),
            "metric_derivations": metrics[-16:],
            "key_metric_catalog": [
                {"metric_key": "sanguo.account.prosperity", "label": "繁荣度"},
                {"metric_key": "sanguo.account.power", "label": "势力值"},
                {"metric_key": "sanguo.account.fame", "label": "名望"},
                {"metric_key": "sanguo.resource.wood", "label": "木材"},
                {"metric_key": "sanguo.resource.iron", "label": "铁矿"},
                {"metric_key": "sanguo.resource.stone", "label": "石料"},
                {"metric_key": "sanguo.resource.food", "label": "粮食"},
                {"metric_key": "sanguo.resource.copper", "label": "铜币"},
                {"metric_key": "sanguo.resource.gold", "label": "黄金"},
                {"metric_key": "sanguo.city.hall_level", "label": "君王殿等级"},
                {"metric_key": "sanguo.action.bandit_remaining", "label": "讨贼剩余"},
                {"metric_key": "sanguo.action.till_remaining", "label": "屯田剩余"},
            ],
            "metric_observation_policy": {
                "accepted_sources": ["canonical_screenshot_region_ocr"],
                "minimum_ocr_confidence": 0.95,
                "exact_integer_required": True,
                "agent_summary_as_value": False,
                "source_artifact_openable": True,
            },
            "budget": console.get("budget"),
            "tasks": (console.get("tasks") or [])[:20],
            "blockers": console.get("blockers") or [],
            "advisories": console.get("advisories") or [],
        },
        "operations": operations,
        "behavior_model": {
            "kind": "learned-semantic-state-graph",
            "display_name": "AI 学会的游戏界面与操作路线",
            "available": bool(nodes or edges),
            "explanation": (
                "这不是神经网络结构，也不是模型隐藏思维链。它是 Game Observatory "
                "根据真实游戏画面和操作结果积累的路线地图：AI 认为自己在哪个界面、"
                "做什么操作，以及操作后到了哪里。"
            ),
            "current_state_id": (
                console.get("current_state", {}).get("id")
                if isinstance(console.get("current_state"), dict)
                else None
            ),
            "nodes": [_slim_state(item) for item in nodes[-32:] if isinstance(item, dict)],
            "edges": edges[-64:],
            "frontier": (console.get("frontier") or [])[:20],
            "skills": (console.get("skills") or [])[:20],
        },
        "instructions": [
            item.model_dump(mode="json")
            for item in instruction_store.list(
                selected_environment_id,
                session_id=selected_session_id,
                limit=30,
            )
        ],
    }


@dataclass(frozen=True)
class LiveFrame:
    content: bytes
    captured_at: str
    sha256: str
    media_type: str = "image/jpeg"


class LiveFrameBroker:
    """Share a low-latency, bandwidth-adaptive ADB stream across LAN viewers."""

    def __init__(
        self,
        player: AIPlayerStore,
        *,
        cache_seconds: float = 0.5,
        preferred_fps: float = 1.0,
        minimum_fps: float = 0.5,
        max_total_mbps: float | None = None,
    ) -> None:
        self.player = player
        self.cache_seconds = max(0.02, cache_seconds)
        self.preferred_fps = max(1.0, min(float(preferred_fps), 8.0))
        self.minimum_fps = max(0.5, min(float(minimum_fps), self.preferred_fps))
        configured_mbps = os.environ.get("OMNI_GAME_LIVE_MAX_STREAM_MBPS")
        self.max_total_mbps = max(
            1.0,
            float(
                max_total_mbps
                if max_total_mbps is not None
                else configured_mbps or 40.0
            ),
        )
        self._lock = threading.RLock()
        self._frames: dict[str, tuple[float, LiveFrame]] = {}
        self._adapters: dict[str, AdbAdapter] = {}
        self._viewer_counts: dict[str, int] = {}
        self._capture_durations: dict[str, deque[float]] = {}
        self._last_frame_sizes: dict[str, int] = {}
        self.fallback_max_width = max(
            640,
            int(os.environ.get("OMNI_GAME_LIVE_FALLBACK_MAX_WIDTH", "1280")),
        )
        self.fallback_jpeg_quality = max(
            30,
            min(80, int(os.environ.get("OMNI_GAME_LIVE_FALLBACK_JPEG_QUALITY", "55"))),
        )

    def serial_for_environment(self, environment_id: str) -> str:
        environment = self.player.get_environment(environment_id)
        if environment is None:
            raise KeyError(f"unknown AI-player environment: {environment_id}")
        prefix = "device://adb/"
        if not environment.device_scope_id.startswith(prefix):
            raise ValueError("live frame streaming currently requires an ADB-backed environment")
        return environment.device_scope_id.removeprefix(prefix)

    def frame(self, environment_id: str) -> LiveFrame:
        serial = self.serial_for_environment(environment_id)
        now = time.monotonic()
        with self._lock:
            cached = self._frames.get(serial)
            if cached and now - cached[0] <= self.cache_seconds:
                return cached[1]
            adapter = self._adapters.get(serial)
            if adapter is None:
                adapter = AdbAdapter(self.player.observatory_store)
                adapter.serial = serial
                self._adapters[serial] = adapter
            capture_started = time.monotonic()
            try:
                content = adapter.observe_probe_jpeg()
                media_type = "image/jpeg"
            except (AdapterError, ValueError):
                content = adapter.observe_probe_frame()
                media_type = "image/png"
            capture_duration = time.monotonic() - capture_started
            frame = LiveFrame(
                content=content,
                captured_at=_utc_now(),
                sha256=hashlib.sha256(content).hexdigest(),
                media_type=media_type,
            )
            self._frames[serial] = (time.monotonic(), frame)
            durations = self._capture_durations.setdefault(serial, deque(maxlen=20))
            durations.append(capture_duration)
            return frame

    def _bandwidth_fallback_frame(self, frame: LiveFrame) -> LiveFrame:
        try:
            with Image.open(BytesIO(frame.content)) as source:
                image = source.convert("RGB")
                if image.width > self.fallback_max_width:
                    height = round(image.height * self.fallback_max_width / image.width)
                    image = image.resize(
                        (self.fallback_max_width, height),
                        Image.Resampling.LANCZOS,
                    )
                output = BytesIO()
                image.save(
                    output,
                    format="JPEG",
                    quality=self.fallback_jpeg_quality,
                    optimize=True,
                )
        except (OSError, UnidentifiedImageError):
            return frame
        content = output.getvalue()
        return LiveFrame(
            content=content,
            captured_at=frame.captured_at,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="image/jpeg",
        )

    def _adaptive_fps(self, serial: str, *, requested_fps: float) -> float:
        requested = max(self.minimum_fps, min(requested_fps, self.preferred_fps))
        viewers = max(1, self._viewer_counts.get(serial, 0))
        frame_size = self._last_frame_sizes.get(serial, 90_000)
        requested_mbps = frame_size * requested * viewers * 8 / 1_000_000
        if requested_mbps <= self.max_total_mbps:
            return requested
        scaled = requested * self.max_total_mbps / requested_mbps
        return max(self.minimum_fps, min(requested, scaled))

    def status(self, environment_id: str) -> dict[str, Any]:
        serial = self.serial_for_environment(environment_id)
        with self._lock:
            viewers = self._viewer_counts.get(serial, 0)
            fps = self._adaptive_fps(serial, requested_fps=self.preferred_fps)
            size = self._last_frame_sizes.get(serial, 0)
            durations = self._capture_durations.get(serial, ())
            average_capture_ms = (
                round(sum(durations) / len(durations) * 1000, 1) if durations else None
            )
            return {
                "schema": "game-observatory.ai-player.live-stream-status.v1",
                "mode": "adaptive",
                "preferred_fps": self.preferred_fps,
                "current_target_fps": round(fps, 2),
                "minimum_fps": self.minimum_fps,
                "active_viewers": viewers,
                "last_frame_bytes": size or None,
                "estimated_total_mbps": (
                    round(size * fps * max(viewers, 1) * 8 / 1_000_000, 2)
                    if size
                    else None
                ),
                "configured_total_budget_mbps": self.max_total_mbps,
                "average_capture_ms": average_capture_ms,
                "capture_path": "adb-screenshot-720p-low-bandwidth-fallback",
                "policy": "failure fallback only; primary playback uses the 60 FPS encoded stream",
            }

    def mjpeg(
        self,
        environment_id: str,
        *,
        interval_seconds: float = 0.25,
    ) -> Iterator[bytes]:
        serial = self.serial_for_environment(environment_id)
        boundary = b"frame"
        requested_fps = min(self.preferred_fps, 1.0 / max(0.1, interval_seconds))
        client_fps = requested_fps
        pressure_streak = 0
        recovery_streak = 0
        with self._lock:
            self._viewer_counts[serial] = self._viewer_counts.get(serial, 0) + 1
        try:
            while True:
                cycle_started = time.monotonic()
                fps = self._adaptive_fps(serial, requested_fps=client_fps)
                try:
                    frame = self._bandwidth_fallback_frame(self.frame(environment_id))
                    with self._lock:
                        self._last_frame_sizes[serial] = len(frame.content)
                    yield (
                        b"--"
                        + boundary
                        + b"\r\nContent-Type: "
                        + frame.media_type.encode("ascii")
                        + b"\r\nContent-Length: "
                        + str(len(frame.content)).encode("ascii")
                        + b"\r\nX-Frame-SHA256: "
                        + frame.sha256.encode("ascii")
                        + b"\r\nX-Stream-Target-FPS: "
                        + f"{fps:.2f}".encode("ascii")
                        + b"\r\n\r\n"
                        + frame.content
                        + b"\r\n"
                    )
                except (AdapterError, KeyError, OSError, ValueError) as exc:
                    message = _compact_text(str(exc), limit=500).encode(
                        "utf-8", "replace"
                    )
                    yield (
                        b"--"
                        + boundary
                        + b"\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "
                        + str(len(message)).encode("ascii")
                        + b"\r\n\r\n"
                        + message
                        + b"\r\n"
                    )
                cycle_elapsed = time.monotonic() - cycle_started
                target_interval = 1.0 / fps
                if cycle_elapsed > target_interval * 1.8:
                    pressure_streak += 1
                    recovery_streak = 0
                    if pressure_streak >= 3:
                        client_fps = max(self.minimum_fps, client_fps * 0.75)
                        pressure_streak = 0
                else:
                    pressure_streak = 0
                    recovery_streak += 1
                    if recovery_streak >= 12 and client_fps < requested_fps:
                        client_fps = min(requested_fps, client_fps + 0.5)
                        recovery_streak = 0
                remaining = target_interval - cycle_elapsed
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            with self._lock:
                remaining_viewers = max(0, self._viewer_counts.get(serial, 1) - 1)
                self._viewer_counts[serial] = remaining_viewers


__all__ = [
    "GameDomainReviewerAgent",
    "LiveFrameBroker",
    "LiveInstructionStore",
    "SpectatorInstructionSubmissionV1",
    "build_live_room_projection",
    "compose_round_instruction",
]
