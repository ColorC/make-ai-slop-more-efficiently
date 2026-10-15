"""Strict versioned contracts for filesystem replay records."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with microsecond precision."""

    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True
    )


class _RunRecordV1(_FrozenModel):
    run_id: str
    t_mono_ns: int = Field(default_factory=time.monotonic_ns, ge=0)
    t_wall: str = Field(default_factory=utc_now)


class RunCountsV1(_FrozenModel):
    frames: int = Field(default=0, ge=0)
    states: int = Field(default=0, ge=0)
    bi: int = Field(default=0, ge=0)
    actions: int = Field(default=0, ge=0)
    timeline: int = Field(default=0, ge=0)


class RunManifestV1(_RunRecordV1):
    schema_id: Literal["game-observatory.replay.run-manifest.v1"] = Field(
        default="game-observatory.replay.run-manifest.v1", alias="schema"
    )
    game_id: str
    environment_id: str
    build_scope_id: str
    device_serial: str
    started_at: str
    ended_at: str | None = None
    end_reason: Literal["completed", "crashed", "quota", "operator_stop", "unknown"] | None = (
        None
    )
    producer: str
    summary: dict[str, Any] = Field(default_factory=dict)
    counts: RunCountsV1 = Field(default_factory=RunCountsV1)
    bytes_total: int = Field(default=0, ge=0)


class FrameRecordV1(_RunRecordV1):
    schema_id: Literal["game-observatory.replay.frame.v1"] = Field(
        default="game-observatory.replay.frame.v1", alias="schema"
    )
    frame_id: str
    path: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    sha256: str | None = None
    status: Literal["pending", "written", "lost"] = "pending"
    bytes: int | None = Field(default=None, ge=0)
    source_format: Literal["png", "webp", "raw_rgb", "raw_bgr", "raw_rgba"] = "png"
    trigger: Literal["interval", "pre_action", "post_action", "state_change", "manual"]
    action_id: str | None = None


class StateFrameV1(_RunRecordV1):
    schema_id: Literal["game-observatory.replay.state-frame.v1"] = Field(
        default="game-observatory.replay.state-frame.v1", alias="schema"
    )
    source: Literal[
        "lua_hook",
        "bi_hook",
        "save_diff",
        "il2cpp",
        "ocr_fallback",
        "planner",
    ]
    payload: dict[str, Any]
    latency_ms: float | None = None


class BIEventV1(_RunRecordV1):
    schema_id: Literal["game-observatory.replay.bi-event.v1"] = Field(
        default="game-observatory.replay.bi-event.v1", alias="schema"
    )
    event: str
    payload: dict[str, Any]


class ActionRecordV1(_RunRecordV1):
    schema_id: Literal["game-observatory.replay.action-record.v1"] = Field(
        default="game-observatory.replay.action-record.v1", alias="schema"
    )
    action_id: str
    kind: Literal["tap", "swipe", "drag", "wait"]
    intent: str
    params: dict[str, Any]
    pre_frame_id: str | None = None
    post_frame_ids: list[str] = Field(default_factory=list)
    pre_state_seq: int | None = None
    post_state_seq: int | None = None
    duration_ms: float
    outcome: str | None = None


class TimelineEventV1(_RunRecordV1):
    schema_id: Literal["game-observatory.replay.timeline-event.v1"] = Field(
        default="game-observatory.replay.timeline-event.v1", alias="schema"
    )
    kind: Literal["frame", "state", "bi", "action", "note", "alert"]
    ref: str | int | None
    note: str | None = None


class RunIndexEntryV1(RunManifestV1):
    schema_id: Literal["game-observatory.replay.run-index-entry.v1"] = Field(
        default="game-observatory.replay.run-index-entry.v1", alias="schema"
    )
    evicted_at: str | None = None
    evicted_reason: Literal["quota"] | None = None
    run_dir_sha256_manifest: str
    hash_reused: bool = False

    @classmethod
    def from_manifest(
        cls,
        manifest: RunManifestV1,
        *,
        run_dir_sha256_manifest: str,
        evicted_at: str | None = None,
        evicted_reason: Literal["quota"] | None = None,
        hash_reused: bool = False,
    ) -> RunIndexEntryV1:
        values = manifest.model_dump(mode="python")
        values["schema"] = "game-observatory.replay.run-index-entry.v1"
        return cls(
            **values,
            evicted_at=evicted_at,
            evicted_reason=evicted_reason,
            run_dir_sha256_manifest=run_dir_sha256_manifest,
            hash_reused=hash_reused,
        )


class ReplayTimingV1(_FrozenModel):
    active_scan_last_ms: float = Field(default=0.0, ge=0)
    active_scan_count: int = Field(default=0, ge=0)
    ensure_capacity_last_ms: float = Field(default=0.0, ge=0)
    ensure_capacity_count: int = Field(default=0, ge=0)
    eviction_hash_last_s: float = Field(default=0.0, ge=0)


class ReplayStatusV1(_FrozenModel):
    schema_id: Literal["game-observatory.replay.status.v1"] = Field(
        default="game-observatory.replay.status.v1", alias="schema"
    )
    bytes_total: int = Field(ge=0)
    limit: int = Field(ge=0)
    runs: int = Field(ge=0)
    active_run_ids: list[str]
    last_gc: str | None = None
    timing: ReplayTimingV1 = Field(default_factory=ReplayTimingV1)


class OrphanAdoptionReport(_FrozenModel):
    adopted_run_ids: list[str]
    skipped_recent_run_ids: list[str]
    dry: bool


class EvictionReport(_FrozenModel):
    before_bytes: int = Field(ge=0)
    after_bytes: int = Field(ge=0)
    evicted_run_ids: list[str]
    freed_bytes: int = Field(ge=0)
    dry: bool


__all__ = [
    "ActionRecordV1",
    "BIEventV1",
    "EvictionReport",
    "FrameRecordV1",
    "OrphanAdoptionReport",
    "ReplayStatusV1",
    "ReplayTimingV1",
    "RunCountsV1",
    "RunIndexEntryV1",
    "RunManifestV1",
    "StateFrameV1",
    "TimelineEventV1",
]
