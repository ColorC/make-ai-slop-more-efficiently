"""Versioned contracts for the Game Observatory delegation actuator."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ActionKind = Literal["tap", "swipe", "drag", "wait"]
FrameMode = Literal["png", "raw"]
SettleMode = Literal["fixed", "stable"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActionRequestV1(_FrozenModel):
    """One validated device action in the fixed 1080 x 1920 coordinate space."""

    kind: ActionKind
    intent: str = Field(min_length=1)
    params: dict[str, int]
    capture_pre: bool = True
    capture_post: bool = True
    settle: SettleMode = "fixed"

    @model_validator(mode="after")
    def validate_params(self) -> ActionRequestV1:
        required = {
            "tap": {"x", "y"},
            "swipe": {"x1", "y1", "x2", "y2", "duration_ms"},
            "drag": {"x1", "y1", "x2", "y2", "duration_ms"},
            "wait": {"ms"},
        }[self.kind]
        actual = set(self.params)
        if actual != required:
            missing = sorted(required - actual)
            extra = sorted(actual - required)
            raise ValueError(f"invalid {self.kind} params; missing={missing}, extra={extra}")

        for name, value in self.params.items():
            if isinstance(value, bool):
                raise ValueError(f"{name} must be an integer, not bool")

        x_names = ("x",) if self.kind == "tap" else ("x1", "x2")
        y_names = ("y",) if self.kind == "tap" else ("y1", "y2")
        if self.kind != "wait":
            for name in x_names:
                if not 0 <= self.params[name] < 1080:
                    raise ValueError(f"{name} must be in the 1080-wide coordinate space")
            for name in y_names:
                if not 0 <= self.params[name] < 1920:
                    raise ValueError(f"{name} must be in the 1920-high coordinate space")

        if self.kind == "drag" and self.params["duration_ms"] < 300:
            raise ValueError("drag duration_ms must be at least 300")
        if self.kind == "swipe" and self.params["duration_ms"] <= 0:
            raise ValueError("swipe duration_ms must be positive")
        if self.kind == "wait" and self.params["ms"] < 0:
            raise ValueError("wait ms must be non-negative")
        return self


class ActuatorTimingV1(_FrozenModel):
    """Non-overlapping latency layers measured by one actuator action."""

    pre_capture_ms: float = Field(ge=0)
    input_ms: float = Field(ge=0)
    settle_ms: float = Field(ge=0)
    post_capture_ms: float = Field(ge=0)
    store_ms: float = Field(ge=0)
    store_breakdown_ms: dict[str, float] = Field(
        default_factory=lambda: {
            "webp_encode": 0.0,
            "sha256": 0.0,
            "ensure_capacity": 0.0,
            "write_file": 0.0,
        }
    )
    total_ms: float = Field(ge=0)


class ActionResultV1(_FrozenModel):
    """Replay references and timings produced by an action attempt."""

    action_id: str
    pre_frame_id: str | None
    post_frame_ids: list[str]
    timing: ActuatorTimingV1
    settled: bool
    error: str | None = None


class ActuatorConnectionV1(BaseModel):
    """Observed MaaFramework connection and method negotiation details."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    serial: str
    adb_path: str
    maafw_version: str
    screencap_method: str
    input_method: str
    available_screencap_methods: list[str] = Field(default_factory=list)
    available_input_methods: list[str] = Field(default_factory=list)
    resolution: tuple[int, int]
    connect_ms: float = Field(ge=0)
    missing_items: list[str] = Field(default_factory=list)
    controller_info: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ActionKind",
    "ActionRequestV1",
    "ActionResultV1",
    "ActuatorConnectionV1",
    "ActuatorTimingV1",
    "FrameMode",
    "SettleMode",
]
