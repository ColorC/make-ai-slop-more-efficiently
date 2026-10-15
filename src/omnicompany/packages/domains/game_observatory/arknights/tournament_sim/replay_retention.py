# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Replay history retention policy and its observable counters.

Moved verbatim out of ``models.py`` so that file fits the 2000-line
budget; ``models.py`` re-exports every name, so importers are unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, StrictInt, model_validator

from .model_primitives import (
    ControlEffect,
    DeploymentClass,
    Direction,
    EffectRangeOrigin,
    EffectRangeRotation,
    EffectStat,
    EffectTargetSelector,
    GridPoint,
    SkillActivationPolicy,
    SkillSpEvent,
    SkillSpRecoveryMode,
    VisibilityClass,
    _Contract,
)


class ReplayRetentionPolicy(_Contract):
    """Resident replay-history policy; audit exports require ``complete``."""

    mode: Literal["complete", "bounded"] = "complete"
    max_commands: int | None = Field(default=None, ge=0)
    max_events: int | None = Field(default=None, ge=0)
    max_tick_digests: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def limits_match_mode(self) -> "ReplayRetentionPolicy":
        limits = (self.max_commands, self.max_events, self.max_tick_digests)
        if self.mode == "complete" and any(limit is not None for limit in limits):
            raise ValueError("complete replay retention cannot declare resident limits")
        if self.mode == "bounded" and any(limit is None for limit in limits):
            raise ValueError("bounded replay retention requires all resident limits")
        return self


class ReplayRetentionState(_Contract):
    mode: Literal["complete", "bounded"]
    history_complete: bool
    total_commands: int = Field(ge=0)
    total_events: int = Field(ge=0)
    total_tick_digests: int = Field(ge=0)
    resident_commands: int = Field(ge=0)
    resident_events: int = Field(ge=0)
    resident_tick_digests: int = Field(ge=0)
    max_resident_commands: int = Field(ge=0)
    max_resident_events: int = Field(ge=0)
    max_resident_tick_digests: int = Field(ge=0)
