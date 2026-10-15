"""Deterministic 30 TPS battle-frame export for simulator/LIVE alignment."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
from typing import IO, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .engine import BattleEngine, verify_replay
from .models import (
    BattleEvent,
    BattleReplay,
    CommandRecord,
    ContentBundle,
    EnemyState,
    OperatorState,
    ReplayRetentionPolicy,
    ReplayRetentionState,
    SkillDefinition,
    TickStateDigest,
    canonical_sha256,
)


FRAME_AXIS_TPS = 30
UNKNOWN = "unknown"
Unknown = Literal["unknown"]
VerificationMode = Literal["quick", "complete"]


class _FrameContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FramePosition(_FrameContract):
    col_milli: int
    row_milli: int


class FrameSkill(_FrameContract):
    skill_id: str = Field(min_length=1)
    charge: int | Unknown = Field(union_mode="left_to_right")
    state: Literal["charging", "ready", "pending", "armed", "active", "cooldown", "unknown"]


class FrameEnemy(_FrameContract):
    instance_id: str = Field(min_length=1)
    enemy_id: str = Field(min_length=1)
    position: FramePosition | Unknown
    hp: int | Unknown = Field(union_mode="left_to_right")
    status: Literal["moving", "waiting", "blocked", "unknown"]


class FrameOperator(_FrameContract):
    instance_id: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    position: FramePosition | Unknown
    hp: int | Unknown = Field(union_mode="left_to_right")
    skills: tuple[FrameSkill, ...]
    facing: str | Unknown
    block_count: int | Unknown = Field(union_mode="left_to_right")


class BattleFrame(_FrameContract):
    schema_id: Literal["omnicompany.arknights.tournament-sim.frame.v1"] = (
        "omnicompany.arknights.tournament-sim.frame.v1"
    )
    tps: Literal[30] = FRAME_AXIS_TPS
    frame_index: int = Field(ge=0)
    tick: int = Field(ge=0)
    time_numerator: int = Field(ge=0)
    time_denominator: Literal[30] = FRAME_AXIS_TPS
    cost: int | Unknown = Field(union_mode="left_to_right")
    enemies: tuple[FrameEnemy, ...]
    operators: tuple[FrameOperator, ...]
    unknown_fields: tuple[str, ...] = ()


class FrameAxisReceipt(_FrameContract):
    schema_id: Literal["omnicompany.arknights.tournament-sim.frame-axis-receipt.v1"] = (
        "omnicompany.arknights.tournament-sim.frame-axis-receipt.v1"
    )
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_mode: VerificationMode
    replay_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_count: int = Field(ge=1)
    first_frame: int = Field(ge=0)
    last_frame: int = Field(ge=0)
    max_buffered_frames: Literal[1] = 1
    engine_retention_mode: Literal["bounded"] = "bounded"
    engine_max_resident_commands: int = Field(ge=0)
    engine_max_resident_events: int = Field(ge=0)
    engine_max_resident_tick_digests: int = Field(ge=0)


class _StreamingReplayVerifier:
    """Compare a reproduction against a complete source replay without retaining it."""

    def __init__(self, replay: BattleReplay) -> None:
        self.replay = replay
        self.command_index = 0
        self.event_index = 0
        self.tick_digest_index = 0

    @staticmethod
    def _require_match(kind: str, index: int, actual: object, expected: object) -> None:
        if actual != expected:
            raise ValueError(f"frame-axis streaming {kind} mismatch at index {index}")

    def observe_command(self, command: CommandRecord) -> None:
        if self.command_index >= len(self.replay.commands):
            raise ValueError("frame-axis reproduction emitted an extra command")
        self._require_match(
            "command",
            self.command_index,
            command,
            self.replay.commands[self.command_index],
        )
        self.command_index += 1

    def observe_event(self, event: BattleEvent) -> None:
        if self.event_index >= len(self.replay.events):
            raise ValueError("frame-axis reproduction emitted an extra event")
        self._require_match(
            "event",
            self.event_index,
            event,
            self.replay.events[self.event_index],
        )
        self.event_index += 1

    def observe_tick_digest(self, digest: TickStateDigest) -> None:
        if self.tick_digest_index >= len(self.replay.tick_digests):
            raise ValueError("frame-axis reproduction emitted an extra tick digest")
        self._require_match(
            "tick digest",
            self.tick_digest_index,
            digest,
            self.replay.tick_digests[self.tick_digest_index],
        )
        self.tick_digest_index += 1

    def finish(self, engine: BattleEngine) -> ReplayRetentionState:
        consumed = (
            self.command_index,
            self.event_index,
            self.tick_digest_index,
        )
        expected = (
            len(self.replay.commands),
            len(self.replay.events),
            len(self.replay.tick_digests),
        )
        if consumed != expected:
            raise ValueError(
                "frame-axis reproduction ended before consuming all replay history: "
                f"consumed={consumed}, expected={expected}"
            )
        if engine.snapshot.snapshot_hash != self.replay.final_snapshot_hash:
            raise ValueError("frame-axis reproduction final snapshot mismatch")
        retention = engine.replay_retention_state
        if (
            retention.total_commands,
            retention.total_events,
            retention.total_tick_digests,
        ) != expected:
            raise ValueError("frame-axis bounded-history counters do not match source replay")
        return retention


def seconds_to_frame_index(seconds: Decimal | str | int | float) -> int:
    """Map non-negative wall time to the nearest 30 TPS frame, halves upward."""

    try:
        value = seconds if isinstance(seconds, Decimal) else Decimal(str(seconds))
    except InvalidOperation as exc:
        raise ValueError("seconds must be a finite decimal value") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("seconds must be a finite non-negative decimal value")
    return int((value * FRAME_AXIS_TPS).to_integral_value(rounding=ROUND_HALF_UP))


def _enemy_position(content: ContentBundle, enemy: EnemyState) -> FramePosition | Unknown:
    routes = {route.route_id: route for route in content.routes}
    route = routes.get(enemy.route_id)
    points = enemy.path_override or (() if route is None else route.runtime_waypoints)
    if not points or enemy.waypoint_index >= len(points):
        return UNKNOWN
    current = points[enemy.waypoint_index]
    if enemy.waypoint_index >= len(points) - 1:
        return FramePosition(col_milli=current.col * 1000, row_milli=current.row * 1000)
    following = points[enemy.waypoint_index + 1]
    progress = enemy.segment_progress_milli
    return FramePosition(
        col_milli=current.col * 1000 + (following.col - current.col) * progress,
        row_milli=current.row * 1000 + (following.row - current.row) * progress,
    )


def _enemy_status(enemy: EnemyState) -> Literal["moving", "waiting", "blocked"]:
    if enemy.blocked_by is not None:
        return "blocked"
    if enemy.wait_remaining_ticks or enemy.herd_resting_remaining_ticks:
        return "waiting"
    return "moving"


def _skill_frame(
    operator: OperatorState,
    skill_index: int,
    definition: SkillDefinition | None,
) -> FrameSkill:
    skill = operator.skills[skill_index]
    if skill.active_remaining:
        state = "active"
    elif skill.armed:
        state = "armed"
    elif skill.activation_pending:
        state = "pending"
    elif skill.cooldown_remaining:
        state = "cooldown"
    elif definition is None:
        state = "unknown"
    elif skill.sp >= definition.sp_cost * definition.consume_charges:
        state = "ready"
    else:
        state = "charging"
    return FrameSkill(skill_id=skill.skill_id, charge=skill.sp, state=state)


def frame_from_engine(engine: BattleEngine) -> BattleFrame:
    """Project the current judge-private engine state without inventing fields."""

    enemies = tuple(
        FrameEnemy(
            instance_id=enemy.instance_id,
            enemy_id=enemy.enemy_id,
            position=_enemy_position(engine.content, enemy),
            hp=enemy.hp,
            status=_enemy_status(enemy),
        )
        for enemy in sorted(engine.enemies.values(), key=lambda item: item.instance_id)
        if enemy.route_teleport_destination_index is None
    )
    operator_definitions = {item.operator_id: item for item in engine.content.operators}
    operators = tuple(
        FrameOperator(
            instance_id=operator.instance_id,
            operator_id=operator.operator_id,
            position=FramePosition(
                col_milli=operator.position.col * 1000,
                row_milli=operator.position.row * 1000,
            ),
            hp=operator.hp,
            skills=tuple(
                _skill_frame(
                    operator,
                    index,
                    next(
                        (
                            item
                            for item in operator_definitions[operator.operator_id].skills
                            if item.skill_id == skill.skill_id
                        ),
                        None,
                    ),
                )
                for index, skill in enumerate(operator.skills)
            ),
            facing=operator.direction.value,
            block_count=len(operator.blocked_enemy_ids),
        )
        for operator in sorted(engine.operators.values(), key=lambda item: item.instance_id)
    )
    return BattleFrame(
        frame_index=engine.tick,
        tick=engine.tick,
        time_numerator=engine.tick,
        cost=engine.cost,
        enemies=enemies,
        operators=operators,
    )


class _StreamingFrameAxis:
    def __init__(
        self,
        stream: IO[str],
        *,
        content: ContentBundle,
        replay: BattleReplay,
        verification_mode: VerificationMode,
    ) -> None:
        self.stream = stream
        self.content = content
        self.replay = replay
        self.verification_mode = verification_mode
        self.pending: BattleFrame | None = None
        self.frame_count = 0
        self.first_frame: int | None = None
        self.last_frame: int | None = None
        self._write(
            {
                "record_type": "header",
                "schema_id": "omnicompany.arknights.tournament-sim.frame-axis.v1",
                "tps": FRAME_AXIS_TPS,
                "frame_index_formula": "simulator frame_index = logical tick; wall seconds map by round-half-up(seconds * 30)",
                "unknown_policy": "unavailable fields are the literal string 'unknown'; no interpolation",
                "verification_mode": verification_mode,
                "content_hash": content.content_hash,
                "replay_hash": replay.replay_hash,
            }
        )

    def _write(self, value: dict[str, Any]) -> None:
        self.stream.write(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        )

    def observe(self, engine: BattleEngine) -> None:
        frame = frame_from_engine(engine)
        if self.pending is not None and frame.frame_index != self.pending.frame_index:
            self._flush()
        self.pending = frame

    def _flush(self) -> None:
        if self.pending is None:
            return
        self._write({"record_type": "frame", **self.pending.model_dump(mode="json")})
        self.frame_count += 1
        self.first_frame = (
            self.pending.frame_index if self.first_frame is None else self.first_frame
        )
        self.last_frame = self.pending.frame_index
        self.pending = None

    def finish(self, engine: BattleEngine, retention: ReplayRetentionState) -> None:
        self.observe(engine)
        self._flush()
        assert self.first_frame is not None and self.last_frame is not None
        self._write(
            {
                "record_type": "footer",
                "frame_count": self.frame_count,
                "first_frame": self.first_frame,
                "last_frame": self.last_frame,
                "max_buffered_frames": 1,
                "final_snapshot_hash": engine.snapshot.snapshot_hash,
                "engine_retention": retention.model_dump(mode="json"),
            }
        )


def _validate_replay_identity(content: ContentBundle, replay: BattleReplay) -> None:
    if replay.content_hash != content.content_hash:
        raise ValueError("frame-axis replay content hash mismatch")
    body = replay.model_dump(mode="json", exclude={"replay_hash"})
    if replay.replay_hash != canonical_sha256(body):
        raise ValueError("frame-axis replay hash mismatch")


def export_replay_frame_axis(
    content: ContentBundle,
    replay: BattleReplay,
    path: Path,
    *,
    verification_mode: VerificationMode = "quick",
) -> FrameAxisReceipt:
    """Replay once into a one-frame-buffered JSONL axis.

    ``quick`` validates identity and compares the reproduced replay during the
    exporting pass. ``complete`` first performs the independent full replay
    verifier and then performs the exporting pass, intentionally spending the
    second traversal.
    """

    _validate_replay_identity(content, replay)
    if verification_mode == "complete":
        verification = verify_replay(content, replay)
        if not verification.valid:
            raise ValueError("frame-axis complete replay verification failed")
    elif verification_mode != "quick":
        raise ValueError("unknown frame-axis verification mode")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        axis = _StreamingFrameAxis(
            stream,
            content=content,
            replay=replay,
            verification_mode=verification_mode,
        )
        stream_verifier = _StreamingReplayVerifier(replay)
        engine = BattleEngine(
            content,
            replay.initial_state,
            source_wave_package_hash=replay.source_wave_package_hash,
            source_wave_scenario_id=replay.source_wave_scenario_id,
            source_wave_scenario_assignments=replay.source_wave_scenario_assignments,
            replay_retention=ReplayRetentionPolicy(
                mode="bounded",
                max_commands=0,
                max_events=0,
                max_tick_digests=0,
            ),
            command_observer=stream_verifier.observe_command,
            event_observer=stream_verifier.observe_event,
            tick_digest_observer=stream_verifier.observe_tick_digest,
        )
        axis.observe(engine)
        for record in replay.commands:
            engine.step(record.action, tick_observer=axis.observe)
            axis.observe(engine)
        retention = stream_verifier.finish(engine)
        axis.finish(engine, retention)

    payload = path.read_bytes()
    assert axis.first_frame is not None and axis.last_frame is not None
    return FrameAxisReceipt(
        path=path.as_posix(),
        sha256=hashlib.sha256(payload).hexdigest(),
        verification_mode=verification_mode,
        replay_hash=replay.replay_hash,
        frame_count=axis.frame_count,
        first_frame=axis.first_frame,
        last_frame=axis.last_frame,
        engine_max_resident_commands=retention.max_resident_commands,
        engine_max_resident_events=retention.max_resident_events,
        engine_max_resident_tick_digests=retention.max_resident_tick_digests,
    )


__all__ = [
    "BattleFrame",
    "FRAME_AXIS_TPS",
    "FrameAxisReceipt",
    "FrameEnemy",
    "FrameOperator",
    "FramePosition",
    "FrameSkill",
    "UNKNOWN",
    "export_replay_frame_axis",
    "frame_from_engine",
    "seconds_to_frame_index",
]
