# [OMNI] origin=claude domain=game_observatory/arknights type=battle-simulator-contracts status=active
"""Pollution zone runtime state created on enemy death.

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


class PollutedZoneState(_Contract):
    instance_id: str = Field(min_length=1)
    source_enemy_id: str = Field(min_length=1)
    center_col_milli: int
    center_row_milli: int
    radius_milli_tiles: int = Field(gt=0, le=20_000)
    remaining_ticks: int = Field(gt=0)
    pulse_remaining_ticks: int = Field(gt=0, le=30)
    low_ground_damage: int = Field(gt=0)
    high_ground_damage: int = Field(gt=0)
